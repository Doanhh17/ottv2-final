@echo off
title OTTv2 - worker_duong
wsl.exe -d Ubuntu-24.04 --exec bash -lc "if test -f /opt/ottv2/app/scripts/start_worker.py; then cd /opt/ottv2/app && python3 scripts/start_worker.py --name worker_duong; elif test -f ~/ottv2-local/scripts/start_worker.py; then cd ~/ottv2-local && python3 scripts/start_worker.py --name worker_duong; else echo 'Can cap nhat ma nguon. Xem docs/MAY_CHAM.md.'; fi; exec bash"
if errorlevel 1 pause
