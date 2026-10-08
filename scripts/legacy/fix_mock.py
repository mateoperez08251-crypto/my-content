import os

filepath = r'd:\my content\modulo_ia.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

import re
old_mock = re.compile(r'# MOCK: Simulador.*?return jsonify\(\{.*?\}\)', re.DOTALL)

new_code = '''
        models_dir = os.path.join(BASE_DIR, "models", "video_ai")
        gguf_path = os.path.join(models_dir, "llama-3-8b-instruct.Q8_0.gguf")
        
        if not os.path.exists(gguf_path):
            return jsonify({"success": True, "prompt": "[SIMULADO - Descarga el modelo Director IA para generar prompts reales]\\n\\nIdea: " + idea})

        try:
            from llama_cpp import Llama
            # Load model and offload to GPU
            llm = Llama(model_path=gguf_path, n_ctx=2048, n_gpu_layers=-1, verbose=False)
            
            sys_prompt = "Eres un director de cine experto en crear prompts visuales descriptivos."
            user_msg = f"Crea un prompt de video ultra-detallado. Idea: {idea}, Genero: {genero}, Tono: {tono}, Giro: {giro}, Duracion: {duracion}."
            
            output = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=300
            )
            prompt_real = output["choices"][0]["message"]["content"]
            return jsonify({"success": True, "prompt": prompt_real})
            
        except ImportError:
            return jsonify({"error": "La libreria llama-cpp-python no esta instalada. Vuelve a descargar el modelo para instalarla."}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500
'''

if old_mock.search(content):
    content = old_mock.sub(new_code.strip(), content)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Success")
else:
    print("MOCK logic not found!")
