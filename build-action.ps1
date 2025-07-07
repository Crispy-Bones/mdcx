$env:PYTHONPATH = "$PWD\src"

$currentDate = Get-Date -Format "MMdd"

python -m PyInstaller -n "MDCx_$currentDate" -F `
-i resources/Img/MDCx.ico `
-w main.py `
-p "./src" `
--add-data "resources;resources" `
--add-data "libs;." `
--hidden-import socks `
--hidden-import urllib3 `
--hidden-import _cffi_backend `
--collect-all curl_cffi

Write-Output 'Done'