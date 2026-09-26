import os

filepath = r'd:\my content\modulo_ia.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

import re
old_sim = re.compile(r'def simulate_generation\(prompt, out_path\):.*?f\.write\(f"Generated video for prompt: \{prompt\}"\)', re.DOTALL)

new_code = '''def simulate_generation(prompt, out_path):
    """Simulates or actually runs video generation if model exists"""
    import torch
    
    models_dir = os.path.join(BASE_DIR, "models", "video_ai")
    
    # Simple check if diffusers is installed and model exists
    try:
        import diffusers
        from diffusers import HunyuanVideoPipeline
        has_diffusers = True
    except ImportError:
        has_diffusers = False

    # If the user downloaded Hunyuan (we look for any hunyuan model file)
    hunyuan_files = [f for f in os.listdir(models_dir) if "hunyuan" in f.lower()] if os.path.exists(models_dir) else []
    
    if has_diffusers and hunyuan_files and torch.cuda.is_available():
        print(">> Ejecutando generacion real con HunyuanVideo en GPU...")
        try:
            model_file = os.path.join(models_dir, hunyuan_files[0])
            pipe = HunyuanVideoPipeline.from_pretrained(
                "tencent/HunyuanVideo",
                torch_dtype=torch.float16,
                device_map="balanced"
            )
            # Memory optimization for 24GB VRAM
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_slicing()
            
            output = pipe(prompt=prompt, num_frames=16, num_inference_steps=20).frames[0]
            
            from diffusers.utils import export_to_video
            export_to_video(output, out_path, fps=15)
            print(f">> Generacion completada: {out_path}")
            return
        except Exception as e:
            print(f">> Error en generacion Hunyuan: {e}")
            # Fallback a dummy

    # Fallback simulation
    print(">> Usando simulacion de generacion (modelo no instalado o sin GPU)")
    import time
    time.sleep(5)
    with open(out_path, "w") as f:
        f.write(f"Generated video for prompt: {prompt}")'''

if old_sim.search(content):
    content = old_sim.sub(new_code.strip(), content)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Success fixing simulate_generation")
else:
    print("simulate_generation not found!")
