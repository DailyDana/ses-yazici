@echo off
rem Ses-Yazici baslatici. Bu dosya hic guncellenmez; asil is calistir.cmd'dedir.
rem Guncelleyici yeni calistir.cmd'yi calistir.cmd.yeni olarak birakir, burada yerine konur.
cd /d "%~dp0"
if exist "calistir.cmd.yeni" move /y "calistir.cmd.yeni" "calistir.cmd" >nul
call "%~dp0calistir.cmd" %*
