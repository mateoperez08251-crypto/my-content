import os

file_path = r'd:\my content\templates\index.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Inject CSS & JS in <head>
if 'estudio_ia.css' not in content:
    content = content.replace('</head>', '    <link rel="stylesheet" href="/static/css/estudio_ia.css">\n    <script src="/static/js/estudio_ia.js"></script>\n</head>')

# 2. Inject Sidebar item
sidebar_target = '<a href="#" class="nav-item" onclick="openVoiceClonerModal()">'
if 'id="btn-nav-estudio"' not in content:
    content = content.replace(sidebar_target, '<a href="#" class="nav-item" id="btn-nav-estudio" onclick="openEstudioIA()"><i class="ph-duotone ph-film-strip"></i> Estudio Creador IA</a>\n            ' + sidebar_target)

# 3. Inject empty div in dashboard
dashboard_target = '<div class="dashboard-main">'
if 'id="moduleEstudioIA"' not in content:
    content = content.replace(dashboard_target, dashboard_target + '\n            <!-- PANEL: Estudio Creador IA (Inyección dinámica) -->\n            <div class="panel panel-full" id="moduleEstudioIA" style="display: none; padding: 0; background: transparent; border: none; box-shadow: none;"></div>\n')

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Patch applied successfully!')
