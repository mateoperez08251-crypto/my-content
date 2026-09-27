import sys
content = open("content.py", "r", encoding="utf-8").read()

block1 = """<<<<<<< HEAD
            custom_dir = data.get('custom_output_dir', '')
            output_folder = custom_dir if custom_dir else os.path.join(EXEC_DIR, "videos_descargados")
            
            video = yt_downloader.download_video(video, output_dir=output_folder, quality=calidad, cancel_checker=lambda: cancel_requested)
=======
            output_folder = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            video = yt_downloader.download_video(video, output_dir=output_folder, quality=calidad)
>>>>>>> 221dee6d0612bdd1965419718c6fecd666ad57c6"""

rep1 = """            output_folder = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            video = yt_downloader.download_video(video, output_dir=output_folder, quality=calidad, cancel_checker=lambda: cancel_requested)"""

block2 = """<<<<<<< HEAD
            custom_dl = data.get('custom_output_dir', '')
            dl_dir = custom_dl if custom_dl else os.path.join(EXEC_DIR, "videos_descargados")
            source = yt_downloader.download_video(source, output_dir=dl_dir, quality="1440", cancel_checker=lambda: cancel_requested)
=======
            dl_dir = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            source = yt_downloader.download_video(source, output_dir=dl_dir, quality="1440")
>>>>>>> 221dee6d0612bdd1965419718c6fecd666ad57c6"""

rep2 = """            dl_dir = data.get('custom_output_dir', '') or paths.data_path("videos_descargados")
            source = yt_downloader.download_video(source, output_dir=dl_dir, quality="1440", cancel_checker=lambda: cancel_requested)"""

content = content.replace(block1, rep1).replace(block2, rep2)
open("content.py", "w", encoding="utf-8").write(content)
print("Conflictos resueltos.")
