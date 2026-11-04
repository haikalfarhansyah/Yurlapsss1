; Hook NSIS untuk installer YurLaps (electron-builder).
; Tujuannya satu: menyalin YurLaps-Setup.exe (installer Inno) ke folder aplikasi
; supaya mode pembaruan dalam aplikasi (/UPDATEZIP) punya installer yang siap
; dipakai tanpa perlu mengunduh ulang.
!macro customInstall
  ${If} ${FileExists} "${BUILD_RESOURCES_DIR}\..\..\dist\YurLaps-Setup.exe"
    File "/oname=$INSTDIR\YurLaps-Setup.exe" "${BUILD_RESOURCES_DIR}\..\..\dist\YurLaps-Setup.exe"
  ${EndIf}
!macroend
