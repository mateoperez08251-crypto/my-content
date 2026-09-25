[Setup]
AppName=ContentAppPro
AppVersion=1.0
DefaultDirName={pf}\ContentAppPro
DefaultGroupName=ContentAppPro
OutputDir=dist
OutputBaseFilename=Instalador_ContentAppPro
Compression=lzma2/fast
SolidCompression=yes

[Files]
Source: "dist\ContentAppPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Clonar-voz\*"; DestDir: "{app}\Clonar-voz"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "chrome_tiktok\*"; DestDir: "{app}\chrome_tiktok"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "firebase-key.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "client_secrets.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "secrets.json"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\ContentAppPro"; Filename: "{app}\ContentAppPro.exe"
Name: "{commondesktop}\ContentAppPro"; Filename: "{app}\ContentAppPro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un icono en el escritorio"; GroupDescription: "Iconos adicionales:"

[Run]
Filename: "{app}\ContentAppPro.exe"; Description: "Ejecutar ContentAppPro ahora"; Flags: nowait postinstall skipifsilent

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{app}\videos_procesados"; Permissions: users-modify
Name: "{app}\videos_descargados"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\downloads"; Permissions: users-modify
Name: "{app}\Clonar-voz"; Permissions: users-modify
Name: "{app}\chrome_tiktok"; Permissions: users-modify
