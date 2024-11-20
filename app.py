from flask import Flask, render_template, request, redirect, url_for, flash
import os
import pyodbc
import paramiko
import logging
from werkzeug.utils import secure_filename
from azure.cognitiveservices.vision.face import FaceClient
from msrest.authentication import CognitiveServicesCredentials
from PIL import Image

app = Flask(__name__)
app.secret_key = 'ff91935200508524ead9d3e6220966a3'

# Configurações do Azure
FACE_API_KEY = '1ZCQRsPeCOYdgsIGqFSP4DY9ATze48rWxLXu847Ec0fvWbeGCcNHJQQJ99AKACZoyfiXJ3w3AAAKACOGhRlw'
FACE_API_ENDPOINT = 'https://safedoc-servicecog.cognitiveservices.azure.com/'
FACE_CLIENT = FaceClient(FACE_API_ENDPOINT, CognitiveServicesCredentials(FACE_API_KEY))

# Configuração do banco de dados
SERVER = 'sqlserver-safedoc.database.windows.net'
DATABASE = 'SafeDocDb'
USERNAME = 'azureuser'
PASSWORD = 'Admsenac123!'
DRIVER = '{ODBC Driver 17 for SQL Server}'

# Configuração do diretório de uploads
UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS_IMAGES = {'png', 'jpeg', 'jpg'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Garantir que o diretório de uploads exista
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Função para verificar se a extensão do arquivo de imagem é permitida
def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS_IMAGES

# Função para conectar ao banco de dados
def get_db_connection():
    conn = pyodbc.connect(f'DRIVER={DRIVER};SERVER={SERVER};PORT=1433;DATABASE={DATABASE};UID={USERNAME};PWD={PASSWORD}')
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

# Configuração de logging
logging.basicConfig(level=logging.DEBUG)

# Verificar se a imagem é válida (não corrompida e não vazia)
def is_valid_image(image_path):
    try:
        with Image.open(image_path) as img:
            img.verify()  # Verifica se a imagem é válida, sem precisar carregá-la completamente
            img.load()  # Força o carregamento completo da imagem
        return True
    except (IOError, SyntaxError) as e:
        logging.error(f"Imagem inválida ou corrompida: {e}")
        return False

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
                    
                # Verifica se a imagem é válida
                if not is_valid_image(photo_path):
                    flash('A imagem está corrompida ou é inválida.', 'error')
                    logging.debug(f"Imagem não é válida")
                    return redirect(url_for('register'))

                # Verificar o tamanho do arquivo (máximo 4MB)
                if photo.content_length > 4 * 1024 * 1024:
                    flash('A imagem é muito grande. O tamanho máximo permitido é 4 MB.', 'error')
                    logging.debug(f"Imagem muito grande")
                    return redirect(url_for('register'))

                logging.debug(f"Tipo de conteúdo do arquivo: {photo.content_type}")
                logging.debug(f"Tamanho do arquivo: {os.path.getsize(photo_path)} bytes")

                # Abrir o arquivo em modo binário
                try:
                    with open(photo_path, 'rb') as photo_file:
                        logging.debug("Arquivo de imagem aberto corretamente em modo binário.")
                        # Garantir que o arquivo está posicionado no início
                        photo_file.seek(0)
                        
                        if photo_file:
                            logging.debug("Arquivo de imagem é válido e pronto para detecção.")
                            
                            # Tentar detectar rostos usando o modelo de detecção simples
                            try:
                                detected_faces = FACE_CLIENT.face.detect_with_stream(
                                    photo_file,
                                    return_face_id=True,  # Mantém a detecção do ID do rosto
                                    return_face_landmarks=False,  # Não precisa dos marcos faciais
                                    recognition_model='recognition_01',  # Remova isso ou altere conforme necessário
                                    detection_model='detection_01'  # Modelo de detecção simples
                                )
                            except Exception as e:
                                logging.error(f"Erro ao detectar rosto: {str(e)}")
                                if hasattr(e, 'response') and e.response is not None:
                                    logging.error(f"Resposta da API: {e.response.text}")
                                flash(f"Erro ao detectar rosto na foto: {str(e)}", 'error')
                                return redirect(url_for('register'))  # Redireciona no caso de erro

                            if not detected_faces:
                                flash('A foto não contém um rosto válido.', 'error')
                                return redirect(url_for('register'))

                            logging.debug("Rosto detectado com sucesso!")
                        else:
                            flash('Erro ao abrir a foto. O arquivo não é válido.', 'error')
                            return redirect(url_for('register'))

                except Exception as e:
                    flash(f"Erro ao detectar rosto na foto: {str(e)}", 'error')
                    logging.error(f"Erro ao detectar rosto na foto: {str(e)}")
                    return redirect(url_for('register'))

                # Inserir no banco de dados
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO Users (Name, Email, PhotoPath) VALUES (?, ?, ?)", (name, email, photo_path))
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
                remote_document_path_linux = f'/home/azureuser/documentos/{secure_filename(document.filename)}'

                # Enviar a foto para a VM Windows
                send_file_to_vm(vm_windows_ip, vm_user, vm_password, photo_path, remote_photo_path_windows)

                # Salvar o documento e enviar para a VM Linux
                document_filename = secure_filename(document.filename)
                document_path = os.path.join(app.config['UPLOAD_FOLDER'], document_filename)
                document.save(document_path)
                send_file_to_vm(vm_linux_ip, vm_user, vm_password, document_path, remote_document_path_linux)

                flash('Usuário registrado com sucesso e arquivos enviados!', 'success')
                logging.debug("Processo concluído com sucesso!")
                return redirect(url_for('query'))

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
        name = request.form['name']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Users WHERE Name LIKE ?", ('%' + name + '%',))
        users = cursor.fetchall()
        cursor.close()
        return render_template('query.html', users=users)
    return render_template('query.html', users=[])

if __name__ == '__main__':
    app.run(debug=True)
