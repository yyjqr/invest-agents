; 安装前终止旧版进程，避免 .pyd / .dll 文件被占用无法覆盖写入
!macro NSIS_HOOK_PREINSTALL
  DetailPrint "正在关闭旧版应用..."
  nsExec::Exec 'taskkill /F /IM app.exe /T'
  nsExec::Exec 'taskkill /F /IM investMaster-App.exe /T'
  ; 主进程被强制杀死后，Python/uvicorn 子进程会成为孤儿并继续占用 8765 端口。
  ; 下一行找到监听 8765 的进程并强制终止，确保新版 Python 后端能正常启动。
  ; 注意：NSIS 字符串中 $ 是变量前缀，必须用 $$ 转义为字面量 $，
  ;       否则 $$_ 会被展开为空字符串，导致 PowerShell 命令失败。
  nsExec::Exec 'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -EA 0 | ForEach-Object { Stop-Process -Id $$_.OwningProcess -Force -EA 0 }"'
  ; 等待进程释放文件句柄
  Sleep 2000
  ; 清理旧版残留目录。之前的安装可能在 invest-app/ 下留下了
  ; invest_app/、.venv/、tests/ 等 Python 源码目录。
  ; uvicorn 默认将 cwd 加入 sys.path，旧的 invest_app/ 会遮蔽
  ; site-packages 中的新版本，导致服务器运行旧代码（无 build 标记→404）。
  RMDir /r "$INSTDIR\invest-app\invest_app"
  RMDir /r "$INSTDIR\invest-app\.venv"
  RMDir /r "$INSTDIR\invest-app\tests"
  RMDir /r "$INSTDIR\invest-app\.pytest_cache"
  RMDir /r "$INSTDIR\invest-app\invest_app.egg-info"
!macroend

!macro NSIS_HOOK_POSTINSTALL
!macroend

!macro NSIS_HOOK_PREUNINSTALL
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
!macroend
