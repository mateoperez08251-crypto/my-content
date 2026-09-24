import os

def install_startup():
    # Obtener la carpeta de Inicio de Windows usando variables de entorno
    startup_dir = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
    vbs_path = os.path.join(startup_dir, "ContentApp_Radar.vbs")
    
    # Obtener rutas absolutas
    base_dir = os.path.abspath(os.path.dirname(__file__))
    pythonw_exe = os.path.join(base_dir, ".venv", "Scripts", "pythonw.exe")
    content_py = os.path.join(base_dir, "content.py")
    
    if not os.path.exists(pythonw_exe):
        pythonw_exe = "pythonw.exe"
        
    # Contenido del script VBS que inicia pythonw de forma invisible
    vbs_content = f"""Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{base_dir}"
WshShell.Run \"\"\"{pythonw_exe}\"\" \"\"{content_py}\"\" --hidden\", 0, False
"""
    
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
        
    print(f"¡Éxito! Se ha configurado el auto-inicio en:\n{vbs_path}")
    print("La aplicación ahora iniciará automáticamente (oculta en segundo plano) cuando enciendas la PC.")

if __name__ == "__main__":
    install_startup()

