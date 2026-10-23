"""Genera los iconos originales de My Content con Pillow (sin descargas ni fuentes)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'static' / 'icons'
NAVY, CYAN, BLUE, WHITE = '#0B1525', '#00E5EF', '#438FFF', '#F4FBFF'


def dibujar(tipo='app', size=512):
    # Dibujo vectorial a 4x y reducción para bordes suaves incluso en 16 px.
    scale = 4
    image = Image.new('RGBA', (128 * scale, 128 * scale))
    d = ImageDraw.Draw(image)
    def rect(box, radius, color):
        d.rounded_rectangle(tuple(v * scale for v in box), radius=radius * scale, fill=color)
    def polygon(points, color):
        d.polygon([(x * scale, y * scale) for x, y in points], fill=color)
    def line(points, color, width):
        d.line([(x * scale, y * scale) for x, y in points], fill=color, width=width * scale, joint='curve')
    def ellipse(box, color):
        d.ellipse(tuple(v * scale for v in box), fill=color)
    rect((4, 4, 124, 124), 30, NAVY)
    if tipo == 'app':
        # Dos brackets de edición y un play: la silueta sigue legible en la bandeja.
        rect((23, 26, 37, 102), 7, CYAN)
        rect((23, 26, 58, 40), 7, CYAN)
        rect((23, 88, 58, 102), 7, CYAN)
        rect((91, 26, 105, 102), 7, BLUE)
        rect((70, 26, 105, 40), 7, BLUE)
        rect((70, 88, 105, 102), 7, BLUE)
        polygon(((52, 44), (81, 64), (52, 84)), WHITE)
    elif tipo == 'bot':
        line(((64, 25), (64, 40)), CYAN, 6)
        ellipse((59, 18, 69, 28), CYAN)
        rect((23, 38, 105, 102), 18, CYAN)
        rect((30, 45, 98, 95), 12, NAVY)
        rect((15, 59, 23, 81), 4, BLUE)
        rect((105, 59, 113, 81), 4, BLUE)
        rect((42, 57, 51, 69), 4, WHITE)
        rect((77, 57, 86, 69), 4, WHITE)
        rect((49, 80, 79, 85), 2, BLUE)
    elif tipo == 'avisos':
        ellipse((38, 29, 90, 81), CYAN)
        polygon(((38, 55), (90, 55), (91, 76), (102, 91), (26, 91), (37, 76)), CYAN)
        rect((56, 98, 72, 105), 3, WHITE)
        ellipse((82, 22, 108, 48), NAVY)
        ellipse((87, 27, 103, 43), BLUE)
    else:
        raise ValueError(tipo)
    return image.resize((size, size), Image.Resampling.LANCZOS)


SVG_PARTES = {
    'my-content': '<g fill="#00E5EF"><rect x="23" y="26" width="14" height="76" rx="7"/><rect x="23" y="26" width="35" height="14" rx="7"/><rect x="23" y="88" width="35" height="14" rx="7"/></g><g fill="#438FFF"><rect x="91" y="26" width="14" height="76" rx="7"/><rect x="70" y="26" width="35" height="14" rx="7"/><rect x="70" y="88" width="35" height="14" rx="7"/></g><path d="M52 44 81 64 52 84Z" fill="#F4FBFF"/>',
    'bot': '<path d="M64 25V40" stroke="#00E5EF" stroke-width="6"/><circle cx="64" cy="23" r="5" fill="#00E5EF"/><rect x="23" y="38" width="82" height="64" rx="18" fill="#00E5EF"/><rect x="30" y="45" width="68" height="50" rx="12" fill="#0B1525"/><g fill="#438FFF"><rect x="15" y="59" width="8" height="22" rx="4"/><rect x="105" y="59" width="8" height="22" rx="4"/><rect x="49" y="80" width="30" height="5" rx="2"/></g><g fill="#F4FBFF"><rect x="42" y="57" width="9" height="12" rx="4"/><rect x="77" y="57" width="9" height="12" rx="4"/></g>',
    'avisos': '<g fill="#00E5EF"><circle cx="64" cy="55" r="26"/><path d="M38 55H90L91 76 102 91H26L37 76Z"/></g><rect x="56" y="98" width="16" height="7" rx="3" fill="#F4FBFF"/><circle cx="95" cy="35" r="13" fill="#0B1525"/><circle cx="95" cy="35" r="8" fill="#438FFF"/>',
}


def generar():
    OUT.mkdir(parents=True, exist_ok=True)
    for nombre, formas in SVG_PARTES.items():
        (OUT / (nombre + '.svg')).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
            '<rect x="4" y="4" width="120" height="120" rx="30" fill="#0B1525"/>'
            + formas + '</svg>\n', encoding='utf-8')
    app = dibujar()
    app.save(OUT / 'my-content.png')
    app.save(OUT / 'my-content.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])


if __name__ == '__main__':
    generar()
