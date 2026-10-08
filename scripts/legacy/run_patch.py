import sys
import re

with open('d:\\my content\\api_clonador.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace imports
content = content.replace('from fastapi import FastAPI, File, Form, HTTPException, UploadFile', 'from flask import Blueprint, request, jsonify, send_file, render_template, Response, stream_with_context')
content = content.replace('from fastapi.responses import FileResponse, StreamingResponse', '')
content = content.replace('from fastapi.staticfiles import StaticFiles', '')
content = content.replace('import uvicorn', '')
content = content.replace('app = FastAPI(title="Clonador de voz local")', 'clonador_bp = Blueprint("clonador_bp", __name__)')

# Route replacements
content = re.sub(r'@app\.get\("(.*?)"\)', r'@clonador_bp.route("\1", methods=["GET"])', content)
content = re.sub(r'@app\.post\("(.*?)"\)', r'@clonador_bp.route("\1", methods=["POST"])', content)
content = re.sub(r'@app\.delete\("(.*?)"\)', r'@clonador_bp.route("\1", methods=["DELETE"])', content)

with open('d:\\my content\\api_clonador_mod.py', 'w', encoding='utf-8') as f:
    f.write(content)
