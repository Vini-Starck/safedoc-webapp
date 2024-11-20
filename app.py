from flask import Flask, render_template, request, redirect, url_for, flash
import os
import pyodbc
import paramiko
import logging
from werkzeug.utils import secure_filename
from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
import json
import requests

app = Flask(__name__)
app.secret_key = 'ff91935200508524ead9d3e6220966a3'

image_url = 'https://st4.depositphotos.com/6903990/27898/i/450/depositphotos_278981062-stock-photo-beautiful-young-woman-clean-fresh.jpg'

# Configurações do Azure
FACE_API_KEY = '1ZCQRsPeCOYdgsIGqFSP4DY9ATze48rWxLXu847Ec0fvWbeGCcNHJQQJ99AKACZoyfiXJ3w3AAAKACOGhRlw'
FACE_API_ENDPOINT = 'https://safedoc-servicecog.cognitiveservices.azure.com/'

# Configuração do banco de dados
SERVER = 'sqlserver-safedoc.database.windows.net'
DATABASE = 'SafeDocDb'
USERNAME = 'azureuser'
PASSWORD = 'Admsenac123!'
DRIVER = '{ODBC Driver 18 for SQL Server}'

# Configuração do diretório de uploads
UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS_IMAGES = {'png', 'jpeg', 'jpg'}
ALLOWED_EXTENSIONS_DOCS = {'pdf', 'docx', 'txt', 'jpg', 'jpeg'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Garantir que o diretório de uploads exista
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Função para verificar se a extensão do arquivo de imagem é permitida
def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_IMAGES

# Função para verificar se a extensão do arquivo de documento é permitida
def allowed_document_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_DOCS

# Função para conectar ao banco de dados
def get_db_connection():
    conn = pyodbc.connect(f'DRIVER={DRIVER};SERVER={SERVER};PORT=1433;DATABASE={DATABASE};UID={USERNAME};PWD={PASSWORD}')
    
    # Criação da tabela Users, caso não exista
    cursor = conn.cursor()
    cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Users' AND xtype='U')
        CREATE TABLE Users (
            Id INT IDENTITY PRIMARY KEY,
            Name NVARCHAR(100),
            Email NVARCHAR(100),
            PhotoPath NVARCHAR(255),
            DocumentPath NVARCHAR(255)
        )
    """)
    conn.commit()
    cursor.close()
    
    return conn

# Função para enviar arquivos para a VM via SFTP (usando paramiko)
def send_file_to_vm(vm_ip, vm_user, vm_password, file_path, remote_path):
    try:
        transport = paramiko.Transport((vm_ip, 22))
        transport.connect(username=vm_user, password=vm_password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.put(file_path, remote_path)
        sftp.close()
        transport.close()
        logging.debug(f"Arquivo {file_path} enviado com sucesso para {vm_ip}:{remote_path}")
    except Exception as e:
        logging.error(f"Erro ao enviar arquivo para {vm_ip}: {e}")
        raise  # Levanta a exceção para ser capturada no fluxo principal

# Função para detectar rostos na imagem usando a URL
def detect_faces(image_url):
    endpoint = FACE_API_ENDPOINT + 'face/v1.0/detect'
    subscription_key = FACE_API_KEY

    # Parâmetros de requisição
    params = {
        'returnFaceId': 'false',  # Não precisa do FaceId
        'returnFaceLandmarks': 'false'  # Não retornar landmarks
    }

    # Corpo da requisição com a URL da imagem
    body = json.dumps({"url": image_url})

    # Cabeçalhos da requisição
    headers = {
        'Content-Type': 'application/json',
        'Ocp-Apim-Subscription-Key': subscription_key
    }

    # Realiza a requisição POST para detectar o rosto
    response = requests.post(endpoint, params=params, headers=headers, data=body)

    if response.status_code != 200:
        logging.error(f"Erro ao detectar rosto: {response.text}")
        return None

    return response.json()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        logging.debug("Iniciando o processo de registro...")

        name = request.form['name']
        email = request.form['email']
        photo = request.files['photo']
        document = request.files['document']

        try:
            logging.debug(f"Nome: {name}, Email: {email}")

            # Verificar se a foto foi enviada e se é válida
            if photo and allowed_image_file(photo.filename):
                logging.debug(f"Foto recebida: {photo.filename}")
                filename = secure_filename(photo.filename)
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                logging.debug(f"Salvando foto em: {photo_path}")
                
                # Salva a foto no diretório de uploads
                photo.save(photo_path)
                logging.debug(f"{photo_path} salva com sucesso")

                # Verificar se a imagem foi salva corretamente
                if not os.path.exists(photo_path):
                    flash('Erro ao salvar a imagem.', 'error')
                    logging.debug(f"Erro ao salvar imagem")
                    return redirect(url_for('register'))
                    
                # Verificar o tamanho do arquivo (máximo 4MB)
                if photo.content_length > 4 * 1024 * 1024:
                    flash('A imagem é muito grande. O tamanho máximo permitido é 4 MB.', 'error')
                    logging.debug(f"Imagem muito grande")
                    return redirect(url_for('register'))

                # Verificar se o documento foi enviado e se é válido
                if document and allowed_document_file(document.filename):
                    document_filename = secure_filename(document.filename)
                    document_path = os.path.join(app.config['UPLOAD_FOLDER'], document_filename)
                    logging.debug(f"Salvando documento em: {document_path}")
                    
                    # Salva o documento no diretório de uploads
                    document.save(document_path)
                    logging.debug(f"{document_path} salvo com sucesso")

                    # Verificar o tamanho do arquivo (máximo 10MB)
                    if document.content_length > 10 * 1024 * 1024:
                        flash('O documento é muito grande. O tamanho máximo permitido é 10 MB.', 'error')
                        logging.debug(f"Documento muito grande")
                        return redirect(url_for('register'))

                    logging.debug(f"Tipo de conteúdo do arquivo: {document.content_type}")
                    logging.debug(f"Tamanho do arquivo: {os.path.getsize(document_path)} bytes")

                    # Agora, enviar a URL da imagem para a API de detecção de rostos
                    detected_faces = detect_faces(image_url)

                    if not detected_faces:
                        flash('Nenhum rosto detectado na foto.', 'error')
                        return redirect(url_for('register'))

                    logging.debug("Rosto detectado com sucesso!")

                    # Inserir no banco de dados
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO Users (Name, Email, PhotoPath, DocumentPath) VALUES (?, ?, ?, ?)",
                                   (name, email, photo_path, document_path))
                    conn.commit()
                    cursor.close()
                    logging.debug("Usuário inserido no banco de dados com sucesso!")

                    # Enviar os arquivos para as VMs
                    vm_windows_ip = '4.228.63.80'
                    vm_linux_ip = '4.228.63.146'
                    vm_user = 'azureuser'
                    vm_password = 'Admsenac123!'

                    # Caminho remoto para a foto na VM Windows (diretório C:\Users\azureuser\Pictures)
                    remote_photo_path_windows = f'C:/Users/azureuser/Pictures/{filename}'
                    remote_document_path_linux = f'/home/azureuser/documentos/{document_filename}'

                    # Enviar a foto para a VM Windows
                    send_file_to_vm(vm_windows_ip, vm_user, vm_password, photo_path, remote_photo_path_windows)

                    # Enviar o documento para a VM Linux
                    send_file_to_vm(vm_linux_ip, vm_user, vm_password, document_path, remote_document_path_linux)

                    flash('Usuário registrado com sucesso e arquivos enviados!', 'success')
                    logging.debug("Processo concluído com sucesso!")
                    return redirect(url_for('query'))

                else:
                    flash('Por favor, envie um documento válido (pdf, docx, txt).', 'error')
                    return redirect(url_for('register'))

            else:
                flash('Por favor, envie uma foto válida (png, jpg, jpeg).', 'error')
                return redirect(url_for('register'))

        except Exception as e:
            flash(f"Ocorreu um erro durante o registro: {str(e)}", 'error')
            logging.error(f"Erro durante o registro: {str(e)}")
            return redirect(url_for('register'))

    return render_template('register.html')

# Página inicial
@app.route('/')
def index():
    return render_template('index.html')

# Página de consulta
@app.route('/query', methods=['GET', 'POST'])
def query():
    if request.method == 'POST':
        logging.debug("Realizando consulta...")
        # Aqui você pode implementar o código para consulta de dados.
        pass
    return render_template('query.html')

if __name__ == '__main__':
    app.run(debug=True)
