import json

def update_ipynb(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for cell in data.get('cells', []):
        if cell.get('cell_type') == 'code':
            source = cell.get('source', [])
            for i, line in enumerate(source):
                if line == "    texto_abajo: str = Form(\"\")\n":
                    # Add new param here
                    source[i] = "    texto_abajo: str = Form(\"\"),\n"
                    source.insert(i+1, "    minuto_inicio: float = Form(0.0)\n")
                elif line == "    background_tasks.add_task(ejecutar_procesamiento, req_id, base_dir, video_path, wm_path, duracion_clip, num_partes, texto_arriba, texto_abajo)\n":
                    source[i] = "    background_tasks.add_task(ejecutar_procesamiento, req_id, base_dir, video_path, wm_path, duracion_clip, num_partes, texto_arriba, texto_abajo, minuto_inicio)\n"
                elif line == "def ejecutar_procesamiento(req_id, base_dir, video_path, wm_path, duracion_clip, num_partes, texto_arriba, texto_abajo):\n":
                    source[i] = "def ejecutar_procesamiento(req_id, base_dir, video_path, wm_path, duracion_clip, num_partes, texto_arriba, texto_abajo, minuto_inicio=0.0):\n"
                elif line == "            inicio = i * duracion_clip\n":
                    source[i] = "            inicio = (minuto_inicio * 60.0) + (i * duracion_clip)\n"
            
            cell['source'] = source

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    update_ipynb("colab_motor.ipynb")
