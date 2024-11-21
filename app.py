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
import base64
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient

app = Flask(__name__)
app.secret_key = 'ff91935200508524ead9d3e6220966a3'

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
    conn = pyodbc.connect(f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER=sqlserver-safedoc.database.windows.net;PORT=1433;DATABASE=SafeDocDb;UID=azureuser;PWD=Admsenac123!')
    
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

# Função para hospedar arquivos no Azure Blob Storage (geral, tanto para fotos quanto documentos)
def upload_to_blob(file_path, filename, container_name):
    # Credenciais do Azure Blob Storage
    account_name = "blobstoragesafedoc"
    account_key = "xQVjsEgKRYDvP9ZepBg182E8F9NKMvAKuYhn75XHhuMYBnA7Z3EuBcND2P8GyPAF07+J7Z1BA4Xt+AStEE9QOA=="

    # Conectar ao Blob Service Client
    blob_service_client = BlobServiceClient(account_url=f"https://{account_name}.blob.core.windows.net", credential=account_key)
    container_client = blob_service_client.get_container_client(container_name)

    # Definir o nome do arquivo no Blob Storage
    blob_client = container_client.get_blob_client(filename)

    # Enviar o arquivo para o Blob Storage
    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)

    # URL pública do arquivo no Blob Storage
    return f"https://{account_name}.blob.core.windows.net/{container_name}/{filename}"

# Função para detectar rostos na imagem usando a URL
def detect_faces(image_url):
    endpoint = 'https://safedoc-servicecog.cognitiveservices.azure.com/face/v1.0/detect'
    subscription_key = '1ZCQRsPeCOYdgsIGqFSP4DY9ATze48rWxLXu847Ec0fvWbeGCcNHJQQJ99AKACZoyfiXJ3w3AAAKACOGhRlw'

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

# Função para enviar arquivos para a VM Windows
def send_file_to_windows_vm(file_path, destination_path):
    # Conectar à VM Windows usando SSH (paramiko)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('4.228.63.80', username='azureuser', password='Admsenac123!')

    # Enviar o arquivo
    sftp = ssh.open_sftp()
    sftp.put(file_path, destination_path)
    sftp.close()
    ssh.close()

# Função para enviar documentos para a VM Linux
def send_file_to_linux_vm(file_path, destination_path):
    # Conectar à VM Linux usando SSH (paramiko)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('4.228.63.146', username='azureuser', password='Admsenac123!')

    # Enviar o arquivo
    sftp = ssh.open_sftp()
    sftp.put(file_path, destination_path)
    sftp.close()
    ssh.close()


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

                # Hospedar a foto no Azure Blob Storage e obter a URL
                image_url = upload_to_blob(photo_path, filename, "fotos")
                logging.debug(f"Hospedando foto no Blob e obtendo URL")

                # Verificar se a foto contém rosto usando o serviço de IA da Azure
                faces = detect_faces(image_url)
                logging.debug(f"Validando rostos")

                if not faces:
                    flash('A foto não contém um rosto. Por favor, envie uma foto válida com rosto.', 'error')
                    return redirect(url_for('register'))

                # Verificar se o documento foi enviado e se é válido
                if document and allowed_document_file(document.filename):
                    document_filename = secure_filename(document.filename)
                    document_path = os.path.join(app.config['UPLOAD_FOLDER'], document_filename)
                    logging.debug(f"Salvando documento em: {document_path}")
                    
                    # Salva o documento no diretório de uploads
                    document.save(document_path)
                    logging.debug(f"{document_path} salvo com sucesso")

                    # Hospedar o documento no Azure Blob Storage e obter a URL
                    document_url = upload_to_blob(document_path, document_filename, "documentos")
                    logging.debug(f"Hospedando documento no Blob e obtendo URL")

                    # Inserir no banco de dados
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO Users (Name, Email, PhotoPath, DocumentPath) VALUES (?, ?, ?, ?)",
                                   (name, email, image_url, document_url))  # Salva a URL da foto e do documento
                    conn.commit()
                    cursor.close()
                    logging.debug("Usuário inserido no banco de dados com sucesso!")

                    # Enviar os arquivos para as VMs
                    vm_windows_ip = '4.228.63.80'
                    vm_linux_ip = '4.228.63.146'
                    vm_user = 'azureuser'
                    vm_password = 'Admsenac123!'

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
@app.route('/query', methods=['GET'])
def query():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('query.html', users=users)

if __name__ == '__main__':
    app.run(debug=True)
