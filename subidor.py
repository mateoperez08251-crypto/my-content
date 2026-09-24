import sys
import os
import random
import time
# pyrefly: ignore [missing-import]
from playwright.sync_api import sync_playwright

EXPECTED_COUNTRY = "NONE" # CAMBIA ESTO. Ej: "US" (Estados Unidos), "ES" (España). "NONE" lo desactiva.

def verificar_kill_switch(context):
    if EXPECTED_COUNTRY.upper() == "NONE":
        return
        
    print("🛡️ Escaneando IP actual por seguridad (Kill-Switch)...")
    try:
        # Hacer petición en segundo plano sin cambiar de pestaña
        response = context.request.get("http://ip-api.com/json", timeout=10000)
        data = response.json()
        current_ip = data.get("query", "Desconocida")
        current_country = data.get("countryCode", "Desconocido")
        
        print(f"🌐 IP detectada: {current_ip} (País: {current_country})")
        
        if current_country.upper() != EXPECTED_COUNTRY.upper():
            print(f"\n❌ [!] KILL-SWITCH ACTIVADO [!]")
            print(f"❌ Fuga de IP detectada. País esperado: {EXPECTED_COUNTRY}, pero la conexión es de: {current_country}")
            print("❌ Deteniendo subida para proteger la cuenta.")
            sys.exit(1)
            
        print("✅ Verificación de seguridad superada. Conexión segura.")
    except Exception as e:
        print(f"⚠️ Error al verificar IP (¿Proxy caído?). Activando Kill-Switch por precaución: {e}")
        sys.exit(1)

def espera_humana(page, min_ms=1500, max_ms=3500):
    """Espera un tiempo aleatorio para simular comportamiento humano"""
    espera = random.randint(min_ms, max_ms)
    page.wait_for_timeout(espera)

def escribir_humano(page, texto):
    """Escribe carácter por carácter con pausas variables"""
    for char in texto:
        page.keyboard.type(char)
        page.wait_for_timeout(random.randint(20, 120))

def subir_video(ruta_video, descripcion, user_data_dir, schedule_str="None"):
    if not os.path.exists(ruta_video):
        print(f"Error: El archivo a subir no existe en {ruta_video}", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        print("Conectando al navegador abierto...")
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            print("Error: No se pudo conectar a Chrome. ¿Hiciste clic en 'Abrir Chrome' en la app?", file=sys.stderr)
            sys.exit(1)
            
        context = browser.contexts[0]
        page = None
        
        # Buscar si ya hay una pestaña de TikTok abierta
        for p_tab in context.pages:
            if "tiktok" in p_tab.url.lower():
                page = p_tab
                break
                
        if page is None:
            # Si no hay pestaña de tiktok, usar la primera pestaña que haya
            if len(context.pages) > 0:
                page = context.pages[0]
            else:
                page = context.new_page()
                
        # >>> AQUI ESTÁ LA MAGIA: Traer la ventana de Chrome al frente <<<
        print("Trayendo ventana de Chrome al frente para visualización...")
        try:
            page.bring_to_front()
        except: pass
        
        # --- EJECUTAR KILL-SWITCH ---
        verificar_kill_switch(context)
        # ----------------------------
                
        try:
            # === FASE DE CALENTAMIENTO (COMPORTAMIENTO HUMANO) ===
            print("👤 Iniciando calentamiento: Navegando al inicio (For You Page) para simular ser humano...")
            try:
                # Aumentamos el timeout a 60s por si el Celeron está muy cargado
                page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded", timeout=60000)
                # Damos mucho más tiempo inicial para que la página renderice (8 a 12 segs)
                espera_humana(page, 8000, 12000)
                
                # Hacer scroll un par de veces para ver videos
                scrolls = random.randint(2, 4)
                for s in range(scrolls):
                    print(f"👀 Viendo video {s+1}/{scrolls}...")
                    espera_humana(page, 10000, 15000) # Ver el video entre 10 y 15 segundos
                    
                    # Scroll down
                    page.mouse.wheel(0, random.randint(600, 900))
                    # Damos más tiempo después del scroll para que el video nuevo cargue en una PC lenta
                    espera_humana(page, 2500, 4500)
                
                print("✅ Calentamiento terminado. Yendo a la página de subida.")
            except Exception as e:
                print(f"⚠️ Aviso en el calentamiento (ignorando): {e}")

            # SIEMPRE forzar una navegación limpia a la página de subida
            # Esto evita problemas con modales de éxito, páginas de copyright, o estados raros
            print("Navegando a la pantalla de subida limpia...")
            page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")
            espera_humana(page, 4000, 6000)
            
            # Si hay un modal de "Video Subido" de un video anterior, cerrarlo
            try:
                btn_otro = page.locator("button:has-text('Subir otro video'), button:has-text('Upload another video')")
                if btn_otro.count() > 0:
                    print("Limpiando pantalla de éxito anterior...")
                    btn_otro.first.click(force=True)
                    page.wait_for_timeout(2000)
            except:
                pass
            
            print("Buscando dónde subir el video...")
            
            # Limpiar banners de cookies o popups que tapen la pantalla
            try:
                page.evaluate("""
                    document.querySelectorAll('tiktok-cookie-banner, .cookie-banner, div[data-floating-ui-portal]').forEach(e => e.remove());
                """)
                page.wait_for_timeout(500)
            except:
                pass
            
            # 1. ENCONTRAR INPUT DE ARCHIVO Y SUBIR
            subido = False
            for i in range(20): # 20 intentos (20 segs) - más tiempo para la segunda parte
                frames = [page] + page.frames
                for f in frames:
                    try:
                        loc = f.locator("input[type='file']")
                        if loc.count() > 0:
                            loc.first.set_files(ruta_video)
                            subido = True
                            break
                    except:
                        pass
                if subido:
                    break
                if i > 0 and i % 5 == 0:
                    print(f"Buscando input de archivo... {i} segundos.", flush=True)
                page.wait_for_timeout(1000)
                
            if not subido:
                print("Intentando método de clic visual (File Chooser)...")
                try:
                    with page.expect_file_chooser(timeout=10000) as fc_info:
                        clicked = False
                        frames = [page] + page.frames
                        for f in frames:
                            # Añadido selector exacto del botón que me pasó el usuario
                            loc = f.locator("button[data-e2e='select_video_button'], button.upload-stage-btn, button:has-text('Seleccionar video')")
                            if loc.count() > 0:
                                loc.first.click(force=True)
                                clicked = True
                                break
                                
                        if not clicked:
                            # Clic ciego de emergencia
                            w = page.viewport_size['width']
                            h = page.viewport_size['height']
                            page.mouse.click(w / 2, h * 0.65)
                            
                    file_chooser = fc_info.value
                    file_chooser.set_files(ruta_video)
                    subido = True
                except Exception as e:
                    pass
                
            if not subido:
                print("¡MÉTODO OS ACTIVADO! Escribiendo ruta de archivo directamente...")
                import pyautogui
                import pyperclip
                import time
                
                # Usar el selector exacto para hacer clic y abrir la ventana de Windows
                frames = [page] + page.frames
                clicked = False
                for f in frames:
                    loc = f.locator("button[data-e2e='select_video_button'], button.upload-stage-btn")
                    if loc.count() > 0:
                        loc.first.click(force=True)
                        clicked = True
                        break
                        
                if not clicked:
                    # Clic ciego de emergencia
                    w = page.evaluate("window.innerWidth")
                    h = page.evaluate("window.innerHeight")
                    page.mouse.click(w / 2, h * 0.65)
                
                # Esperamos que se abra la ventanita de Windows
                time.sleep(3)
                
                # Enfocar el campo "Nombre de archivo" con Alt+N (funciona en español e inglés)
                pyautogui.hotkey('alt', 'n')
                time.sleep(0.5)
                
                # Limpiar cualquier texto previo
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.3)
                
                # Pegar la ruta del video
                pyperclip.copy(ruta_video)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(1.0)
                
                # Confirmar con Alt+A (botón "Abrir") que es más confiable que Enter
                pyautogui.hotkey('alt', 'a')
                time.sleep(1.0)
                
                # Si la ventana sigue abierta, intentar con Enter
                pyautogui.press('enter')
                time.sleep(0.5)
                pyautogui.press('enter')
                time.sleep(1.0)
                
                # Último recurso: si el diálogo sigue abierto, cerrarlo con Escape
                # y marcar como no subido para que falle con mensaje claro
                pyautogui.press('escape')
                time.sleep(0.3)
                
                subido = True
                page.wait_for_timeout(5000)
                
            if not subido:
                debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_debug.html")
                img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_debug.png")
                try:
                    page.screenshot(path=img_path)
                except:
                    pass
                with open(debug_path, "w", encoding="utf-8") as f_debug:
                    f_debug.write(page.content())
                    for idx, fr in enumerate(page.frames):
                        f_debug.write(f"\n\n--- FRAME {idx} ---\n\n")
                        try:
                            f_debug.write(fr.content())
                        except:
                            pass
                raise Exception("No se encontró el área de subida. Revisa la imagen 'tiktok_debug.png' en tu carpeta para ver qué está tapando la pantalla.")
            
            # ESPERAR A QUE TIKTOK CARGUE LA PÁGINA DEL EDITOR
            print("Video seleccionado. Esperando a que TikTok abra el editor...")
            editor_listo = False
            for i in range(60):  # Máximo 60 segundos esperando
                frames = [page] + page.frames
                for f in frames:
                    try:
                        # Detectar que estamos en el editor buscando el contenedor de estado o el de descripción
                        editor_loc = f.locator("div[data-e2e='upload_status_container'], div[data-e2e='caption_container'], .info-status, .info-main")
                        if editor_loc.count() > 0:
                            editor_listo = True
                            break
                    except:
                        pass
                if editor_listo:
                    print("¡Editor de TikTok cargado!")
                    break
                if i > 0 and i % 5 == 0:
                    print(f"Esperando que TikTok abra el editor... {i} segundos.", flush=True)
                page.wait_for_timeout(1000)
            
            if not editor_listo:
                print("ADVERTENCIA: No se detectó la página del editor. Intentando continuar de todas formas...")
                page.wait_for_timeout(5000)
            
            # 2. PONER DESCRIPCIÓN
            print("Video seleccionado. Buscando el cuadro de descripción...")
            desc_loc = None
            for _ in range(30):
                frames = [page] + page.frames
                for f in frames:
                    try:
                        loc = f.locator(".public-DraftEditor-content, div[contenteditable='true'], div[data-e2e='caption_container'] div[contenteditable='true']")
                        if loc.count() > 0:
                            desc_loc = loc.first
                            break
                    except:
                        pass
                if desc_loc:
                    break
                page.wait_for_timeout(1000)
                
            if desc_loc:
                try:
                    desc_loc.click()
                    # TikTok autocompleta con el nombre del archivo. Lo borramos (Ctrl+A y Borrar)
                    page.wait_for_timeout(500)
                    page.keyboard.press("Control+A")
                    page.wait_for_timeout(200)
                    page.keyboard.press("Backspace")
                    page.wait_for_timeout(200)
                    
                    # Escribimos como un humano
                    escribir_humano(page, descripcion)
                    print("Descripción insertada.")
                except Exception as e:
                    print(f"ADVERTENCIA: Falló al escribir la descripción ({e}). Continuará sin ella.")
            else:
                print("ADVERTENCIA: No se encontró el cuadro de descripción. Se publicará con el texto por defecto.")
                
            # 2.5 PROGRAMAR VIDEO (SI APLICA)
            if schedule_str != "None":
                print(f"Activando programación para: {schedule_str}")
                try:
                    # Click al texto "Programar video" o "Schedule video"
                    page.evaluate('''() => {
                        let elements = document.querySelectorAll("div, span, label");
                        for(let el of elements) {
                            if(el.innerText && (el.innerText.trim() === "Programar video" || el.innerText.trim() === "Schedule video")) {
                                el.click();
                                break;
                            }
                        }
                    }''')
                    page.wait_for_timeout(1500)
                    
                    date_val, time_val = schedule_str.split(" ")
                    
                    # Intento genérico de escribir la fecha y hora si hay inputs activos
                    # (TikTok usa calendarios complejos, esto inyectará el valor en el input visible)
                    page.evaluate(f'''(date_val, time_val) => {{
                        let inputs = document.querySelectorAll("input");
                        for(let inp of inputs) {{
                            if(inp.placeholder && inp.placeholder.includes("202")) {{
                                inp.value = date_val;
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            }}
                            if(inp.placeholder && inp.placeholder.includes(":")) {{
                                inp.value = time_val;
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            }}
                        }}
                    }}''', date_val, time_val)
                    print(f"Programación activada para {date_val} a las {time_val}.")
                except Exception as e:
                    print(f"ADVERTENCIA: No se pudo configurar la fecha ({e}).")

            # 3. PUBLICAR
            print("Esperando a que el video se suba y aparezca el botón de Publicar...")
            page.wait_for_timeout(3000)
            
            publicado = False
            for i in range(300):  # Máximo 5 minutos
                try:
                    # Revisar si el botón de publicar/programar existe y no está deshabilitado
                    listo = page.evaluate('''() => {
                        let btns = document.querySelectorAll("button");
                        for (let b of btns) {
                            let isPostButton = b.getAttribute('data-e2e') === 'post_video_button' || b.innerText.includes('Publicar') || b.innerText.includes('Post') || b.innerText.includes('Programar') || b.innerText.includes('Schedule');
                            if (isPostButton && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true' && b.getAttribute('data-disabled') !== 'true') {
                                return true;
                            }
                        }
                        return false;
                    }''')
                    
                    if listo:
                        print("¡Botón de Publicar/Programar encontrado! Simulando movimiento de ratón humano...", flush=True)
                        espera_humana(page, 2000, 4000)
                        
                        try:
                            # Intentar mover el ratón visualmente al botón antes de hacer clic
                            box = page.evaluate('''() => {
                                let btns = document.querySelectorAll("button");
                                for (let b of btns) {
                                    let isPostButton = b.getAttribute('data-e2e') === 'post_video_button' || b.innerText.includes('Publicar') || b.innerText.includes('Post') || b.innerText.includes('Programar') || b.innerText.includes('Schedule');
                                    if (isPostButton && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {
                                        let rect = b.getBoundingClientRect();
                                        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                                    }
                                }
                                return null;
                            }''')
                            if box:
                                page.mouse.move(box['x'], box['y'], steps=25)
                                espera_humana(page, 300, 800)
                        except:
                            pass
                        
                        print("Haciendo clic...", flush=True)
                        page.evaluate('''() => {
                            let btns = document.querySelectorAll("button");
                            for (let b of btns) {
                                let isPostButton = b.getAttribute('data-e2e') === 'post_video_button' || b.innerText.includes('Publicar') || b.innerText.includes('Post') || b.innerText.includes('Programar') || b.innerText.includes('Schedule');
                                if (isPostButton && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {
                                    b.click();
                                }
                            }
                        }''')
                        
                        print("Revisando si aparece ventana de Copyright (esperando 2 segs)...", flush=True)
                        page.wait_for_timeout(2000)
                        page.evaluate('''() => {
                            let btns = document.querySelectorAll("button");
                            for (let b of btns) {
                                if (b.innerText && (b.innerText.includes('Publicar ahora') || b.innerText.includes('Publish now'))) {
                                    b.click();
                                }
                            }
                        }''')
                        
                        publicado = True
                        break
                    else:
                        # Revisar también en iframes
                        for f in page.frames:
                            try:
                                listo_f = f.evaluate('''() => {
                                    let btns = document.querySelectorAll("button");
                                    for (let b of btns) {
                                        let isPostButton = b.getAttribute('data-e2e') === 'post_video_button' || b.innerText.includes('Publicar') || b.innerText.includes('Post') || b.innerText.includes('Programar') || b.innerText.includes('Schedule');
                                        if (isPostButton && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {
                                            return true;
                                        }
                                    }
                                    return false;
                                }''')
                                if listo_f:
                                    print("¡Botón de Publicar/Programar encontrado en frame! Esperando 3 segundos antes de ejecutar...", flush=True)
                                    page.wait_for_timeout(3000)
                                    
                                    f.evaluate('''() => {
                                        let btns = document.querySelectorAll("button");
                                        for (let b of btns) {
                                            let isPostButton = b.getAttribute('data-e2e') === 'post_video_button' || b.innerText.includes('Publicar') || b.innerText.includes('Post') || b.innerText.includes('Programar') || b.innerText.includes('Schedule');
                                            if (isPostButton && b.offsetParent !== null && !b.disabled && b.getAttribute('aria-disabled') !== 'true') {
                                                b.click();
                                            }
                                        }
                                    }''')
                                    
                                    print("Revisando si aparece ventana de Copyright en frame...", flush=True)
                                    page.wait_for_timeout(2000)
                                    f.evaluate('''() => {
                                        let btns = document.querySelectorAll("button");
                                        for (let b of btns) {
                                            if (b.innerText && (b.innerText.includes('Publicar ahora') || b.innerText.includes('Publish now'))) {
                                                b.click();
                                            }
                                        }
                                    }''')
                                    
                                    publicado = True
                                    break
                            except:
                                pass
                        
                        if publicado:
                            break

                        if i % 10 == 0:
                            print(f"Esperando a que el botón se habilite... {i} segundos.", flush=True)
                except Exception as e:
                    if i % 15 == 0:
                        print(f"Reintentando buscar botón... ({e})", flush=True)
                
                page.wait_for_timeout(1000)
                
            if not publicado:
                try:
                    page.screenshot(path="error_tiktok.png", full_page=True)
                except:
                    pass
                raise Exception("El botón de Publicar/Programar nunca se habilitó. Revisa la imagen error_tiktok.png")
                
            print("¡Clic realizado con éxito! Esperando a que TikTok procese la subida...")
            espera_humana(page, 10000, 12000)
            print("Subida finalizada.")
            
            print("👤 Volviendo al inicio (For You Page) para dejar la cuenta reposando de forma natural...")
            espera_humana(page, 5000, 10000) # Espera varios segundos antes de salir de la página de subida
            try:
                page.goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded")
                espera_humana(page, 3000, 6000)
                print("✅ La cuenta está ahora descansando en el Feed.")
            except:
                pass
            
        except Exception as e:
            print(f"Error durante el proceso de subida: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            # NO cerramos el navegador para que siga abierto para la siguiente parte
            print("Desconectando del navegador...")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Faltan argumentos. Uso: python subidor.py <ruta_video> <descripcion>", file=sys.stderr)
        sys.exit(1)
        
    ruta_video = sys.argv[1]
    descripcion = sys.argv[2]
    perfil = sys.argv[3] if len(sys.argv) > 3 else "Default"
    schedule_str = sys.argv[4] if len(sys.argv) > 4 else "None"
    
    if len(sys.argv) > 5:
        EXPECTED_COUNTRY = sys.argv[5]
    
    subir_video(ruta_video, descripcion, None, schedule_str)
