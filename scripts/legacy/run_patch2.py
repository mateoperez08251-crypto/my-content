import re

with open('d:\\my content\\api_clonador_mod.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix FileResponse
content = re.sub(r'return FileResponse\((.*?)\)', r'return send_file(\1)', content)

# Fix StreamingResponse
content = re.sub(r'return StreamingResponse\((.*?),\s*media_type="text/event-stream",\s*headers=\{.*?\}\)', r'return Response(stream_with_context(\1), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})', content, flags=re.DOTALL)

# Fix async StreamingResponse generators
content = content.replace('async def flujo():', 'def flujo():')
content = content.replace('await asyncio.sleep', 'time.sleep')
content = content.replace('yield f"data:', 'yield f"data:')

# Fix Request JSON data (FastAPI uses dependency injection)
content = re.sub(r'def actualizar_config\(datos: dict\[str, Any\]\)', 'def actualizar_config():\n    datos = request.get_json()', content)
content = re.sub(r'def iniciar_descarga\(datos: dict\[str, Any\]\)', 'def iniciar_descarga():\n    datos = request.get_json()', content)
content = re.sub(r'def generar\(datos: dict\[str, Any\]\)', 'def generar():\n    datos = request.get_json()', content)

# Fix HTTPException
content = re.sub(r'raise HTTPException\((.*?),\s*(.*?)\)', r'return jsonify({"error": \2}), \1', content)

# Fix UploadFile form processing
crear_voz_def = """def crear_voz() -> dict[str, Any]:
    audio = request.files.get('audio')
    nombre = request.form.get('nombre', '')
    transcripcion = request.form.get('transcripcion', '')
"""
content = re.sub(r'async def crear_voz\(.*?\).*?:', crear_voz_def, content, flags=re.DOTALL)

# Fix await audio.read()
content = content.replace('await audio.read()', 'audio.read()')
content = content.replace('audio.filename', 'audio.filename')

# Remove async from routes
content = content.replace('async def actualizar_config', 'def actualizar_config')
content = content.replace('async def progreso_descarga', 'def progreso_descarga')
content = content.replace('async def generar', 'def generar')
content = content.replace('async def eventos', 'def eventos')

# Add missing import
content = content.replace('import time\n', 'import time\nfrom flask import abort\n')

# Fix static routes and uvicorn run
content = re.sub(r'app\.mount\("/static".*?\n', '', content)
content = re.sub(r'if __name__ == "__main__":.*', '', content, flags=re.DOTALL)
content = re.sub(r'def raiz\(\) -> FileResponse:\n    return FileResponse\(DIR_ESTATICO / "index.html"\)', 'def raiz():\n    return render_template("clonador_voz.html")', content)

with open('d:\\my content\\api_clonador_flask.py', 'w', encoding='utf-8') as f:
    f.write(content)
