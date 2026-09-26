[Setup]
AppName=ContentAppPro
AppVersion=2.0
AppPublisher=ContentAppPro
DefaultDirName={pf}\ContentAppPro
DefaultGroupName=ContentAppPro
OutputDir=dist
OutputBaseFilename=Instalador_ContentAppPro
Compression=lzma2/fast
SolidCompression=yes
PrivilegesRequired=admin

[Files]
; === CORE: Ejecutable compilado por PyInstaller ===
Source: "dist\ContentAppPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; === CARPETAS COMPLETAS ===
Source: "Clonar-voz\*"; DestDir: "{app}\Clonar-voz"; Excludes: "__pycache__\,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "chrome_tiktok\*"; DestDir: "{app}\chrome_tiktok"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "VideoAI\*"; DestDir: "{app}\VideoAI"; Excludes: "__pycache__\,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "firebase_radar\*"; DestDir: "{app}\firebase_radar"; Excludes: "functions\venv\,__pycache__\,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

; === MÓDULOS PYTHON PRINCIPALES ===
Source: "content.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "modulo_ia.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "editor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "smart_editor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "api_subidor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "api_clonador_flask.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "audio_extractor.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "audio_separator.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "yt_downloader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "youtube_uploader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "descargar_modelo.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "generate_prompt_variation.py"; DestDir: "{app}"; Flags: ignoreversion

; === CONFIGURACIÓN Y CREDENCIALES ===
Source: "firebase-key.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "firebase.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "client_secrets.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "secrets.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json"; DestDir: "{app}"; Flags: ignoreversion

; === ARCHIVOS DE DATOS ===
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "dataset_director.jsonl"; DestDir: "{app}"; Flags: ignoreversion
Source: "styles_pool.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "dummy.wav"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo_b64.txt"; DestDir: "{app}"; Flags: ignoreversion

; === BINARIOS Y UTILIDADES ===
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "haarcascade_frontalface_default.xml"; DestDir: "{app}"; Flags: ignoreversion
Source: "instalar_dependencias.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "instalar_git.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ContentAppPro"; Filename: "{app}\ContentAppPro.exe"
Name: "{commondesktop}\ContentAppPro"; Filename: "{app}\ContentAppPro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un icono en el escritorio"; GroupDescription: "Iconos adicionales:"

[Run]
Filename: "{app}\ContentAppPro.exe"; Description: "Ejecutar ContentAppPro ahora"; Flags: nowait postinstall skipifsilent

[Dirs]
; Carpetas que la app necesita escribir en runtime
Name: "{app}"; Permissions: users-modify
Name: "{app}\videos_procesados"; Permissions: users-modify
Name: "{app}\videos_descargados"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\downloads"; Permissions: users-modify
Name: "{app}\downloads\audio"; Permissions: users-modify
Name: "{app}\Clonar-voz"; Permissions: users-modify
Name: "{app}\chrome_tiktok"; Permissions: users-modify
Name: "{app}\VideoAI"; Permissions: users-modify
Name: "{app}\models"; Permissions: users-modify
Name: "{app}\models\video_ai"; Permissions: users-modify
Name: "{app}\voces"; Permissions: users-modify
Name: "{app}\salidas"; Permissions: users-modify
Name: "{app}\uploads"; Permissions: users-modify
Name: "{app}\assets_subidos"; Permissions: users-modify
Name: "{app}\firebase_radar"; Permissions: users-modify

[UninstallDelete]
; Limpiar archivos generados en runtime al desinstalar
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\uploads"
