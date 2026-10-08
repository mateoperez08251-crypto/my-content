import sys

with open('templates/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

target = '</iframe>\n        </div>\n    </div>'
replacement = '''</iframe>
        </div>
    </div>

    <!-- Compatibility Modal -->
    <div id="compatibilityModal" style="display: none; align-items: center; justify-content: center; z-index: 100000; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.9); backdrop-filter: blur(15px);">
        <div class="modal" style="width: 90vw; max-width: 600px; text-align: center; border-radius: 16px; border: 1px solid rgba(255,255,255,0.1); background: #1a1a1a; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px 20px;">
                <i class="ph-duotone ph-cpu" style="font-size: 64px; color: white; margin-bottom: 10px;"></i>
                <h2 style="color: white; margin: 0; font-size: 24px;">Análisis de Compatibilidad IA</h2>
            </div>
            
            <div id="compat-intro" style="padding: 40px 30px;">
                <p style="font-size: 16px; color: #ccc; margin-bottom: 30px; line-height: 1.6;">
                    Antes de usar las funciones de Inteligencia Artificial local por primera vez, vamos a hacer un rápido escaneo a tu computadora para asegurarnos de que la experiencia sea la mejor.
                </p>
                <button onclick="runCompatibilityTest()" style="background: #667eea; color: white; border: none; padding: 15px 30px; font-size: 16px; font-weight: bold; border-radius: 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 10px; transition: transform 0.2s;">
                    <i class="ph-bold ph-rocket-launch"></i> Iniciar Escaneo
                </button>
            </div>

            <div id="compat-loading" style="display: none; padding: 50px 30px;">
                <div class="loader" style="width: 40px; height: 40px; border-width: 4px; margin: 0 auto 20px auto; border-top-color: #667eea;"></div>
                <h3 style="color: white; margin-bottom: 10px;">Escaneando hardware...</h3>
                <p style="color: #888;">Verificando Procesador, Memoria RAM y Tarjeta Gráfica</p>
            </div>

            <div id="compat-result" style="display: none; padding: 40px 30px;">
                <div id="compat-icon" style="font-size: 80px; margin-bottom: 20px;"></div>
                <h2 id="compat-message" style="color: white; margin-bottom: 25px; font-size: 22px;"></h2>
                
                <div style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 20px; text-align: left; margin-bottom: 30px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px;">
                        <span style="color: #888;"><i class="ph-duotone ph-memory"></i> Memoria RAM:</span>
                        <span id="compat-ram" style="color: white; font-weight: bold;"></span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px;">
                        <span style="color: #888;"><i class="ph-duotone ph-cpu"></i> Núcleos CPU:</span>
                        <span id="compat-cpu" style="color: white; font-weight: bold;"></span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #888;"><i class="ph-duotone ph-graphics-card"></i> Gráfica:</span>
                        <span id="compat-gpu" style="color: white; font-weight: bold; max-width: 60%; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></span>
                    </div>
                </div>

                <button onclick="closeCompatibilityModal()" style="background: rgba(255,255,255,0.1); color: white; border: 1px solid rgba(255,255,255,0.2); padding: 12px 30px; font-size: 16px; border-radius: 8px; cursor: pointer; transition: all 0.2s;">
                    Entendido, continuar
                </button>
            </div>
        </div>
    </div>'''

if target in text:
    text = text.replace(target, replacement, 1)
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(text)
    print('Replaced successfully')
else:
    print('Target not found')
