; ===========================================================================
;  Inno Setup script — installer YurLaps untuk Windows
;
;  Cara pakai:
;    1. Build dulu aplikasinya:  pyinstaller --noconfirm --clean stintlab.spec
;    2. Buka file ini di Inno Setup 6 (https://jrsoftware.org/isdl.php)
;    3. Build > Compile  ->  dist\YurLaps-Setup-<versi>.exe
;
;  Yang dilakukan installer:
;    * meminta hak Administrator HANYA untuk pemasangan (UAC satu kali),
;    * menyalin aplikasi ke Program Files,
;    * membuat shortcut Start Menu + (opsional) Desktop,
;    * mendukung **pembaruan dalam satu klik**: aplikasi mengunduh zip versi baru
;      lalu menjalankan installer ini dengan /UPDATEZIP=<file.zip>, dan installer
;      memasang versi tersebut tanpa unduh ulang,
;    * menawarkan **pasang driver YurLaps Decoder (WinUSB)** — bisa dilewati,
;      dan bisa diulang kapan saja dari dalam aplikasi (halaman Decoder),
;    * mendaftarkan YurLaps di "Apps & features" supaya mudah di-uninstall.
;
;  Yang TIDAK dilakukan installer: menyentuh data pengguna
;  (%APPDATA%\YurLaps tetap utuh saat uninstall/reinstall).
; ===========================================================================

#define AppName "YurLaps"
#define AppVersion "0.3.0"
#define AppPublisher "YurLaps"
#define AppExeName "YurLaps.exe"

; --- path opsional -----------------------------------------------------------
; Ikon hanya dipakai kalau filenya ada. Tanpa cek ini, Inno berhenti dengan
; "The system cannot find the file specified" hanya karena satu file hilang.
#define HasIcon FileExists(SourcePath + "\assets\yurlaps.ico")

[Setup]
AppId={{7C4E1B62-9C3A-4E7B-9A2D-STINTLAB0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=YurLaps-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Driver butuh admin; aplikasinya sendiri tidak.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
DisableProgramGroupPage=yes
CloseApplications=yes
#if HasIcon
SetupIconFile=..\assets\yurlaps.ico
#endif

[Languages]
; Inno hanya menyediakan pesan bawaan berbahasa Inggris; teks aplikasi tetap
; berbahasa Indonesia.
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Buat shortcut di Desktop"; GroupDescription: "Shortcut:"
Name: "installdriver"; Description: "Pasang driver YurLaps Decoder (WinUSB) — butuh perangkat tercolok"; GroupDescription: "YurLaps Decoder:"; Flags: checkedonce

[Files]
Source: "..\dist\YurLaps\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Setelan & Data ({#AppName})"; Filename: "{app}\{#AppExeName}"; Parameters: "--open-folder"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 1) Pasang driver WinUSB (kalau diminta pengguna)
Filename: "{app}\vendor\win\wdi-simple.exe"; \
  Parameters: "--type 0 --name YurLapsDecoder --manufacturer Realtek --vid 0x0bda --pid 0x2838 --silent"; \
  StatusMsg: "Memasang driver YurLaps Decoder (WinUSB)..."; \
  Flags: runhidden waituntilterminated; Tasks: installdriver; Check: UseWdiSimple

Filename: "{app}\vendor\win\zadig.exe"; \
  StatusMsg: "Membuka Zadig: pilih YurLaps Decoder, lalu klik Install Driver"; \
  Flags: nowait postinstall skipifsilent; Tasks: installdriver; Check: UseZadigAndNotUpdate

; 2) Jalankan aplikasi
;    Saat dipanggil sebagai pembaruan (/UPDATEZIP) aplikasi TIDAK dijalankan:
;    pengguna membuka YurLaps sendiri, jadi tidak ada jendela yang muncul tiba-tiba.
Filename: "{app}\{#AppExeName}"; Description: "Jalankan {#AppName}"; \
  Flags: nowait postinstall skipifsilent; Check: not IsUpdateMode

[UninstallDelete]
; JANGAN hapus %APPDATA%\YurLaps — data sesi & pembalap milik pengguna.
Type: filesandordirs; Name: "{app}\vendor"

[Code]
{ Mode pembaruan: aplikasi memanggil installer ini dengan /UPDATEZIP=<zip>.
  Nilainya dibaca lewat konstanta {param:UPDATEZIP} — tidak perlu loop
  ParamStr/ParamCount di sini, jadi bagian ini tetap sederhana untuk
  dikompilasi (bagian yang rumit = sumber error yang sulit dibaca di CI). }
function UpdateZip: String;
begin
  Result := Trim(ExpandConstant('{param:UPDATEZIP}'));
end;

{ Kutip satu nilai untuk PowerShell: 'teks' }
function PsQuote(const S: String): String;
begin
  Result := '''' + S + '''';
end;

function IsUpdateMode(): Boolean;
begin
  Result := UpdateZip() <> '';
end;

// Entri [Run] hanya dijalankan kalau alatnya benar-benar ikut terpasang.
// (UserInfo: kalau wdi-simple ada -> dipakai; kalau tidak -> Zadig.)
function UseWdiSimple: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\vendor\win\wdi-simple.exe'));
end;

function UseZadig: Boolean;
begin
  Result := (not FileExists(ExpandConstant('{app}\vendor\win\wdi-simple.exe')))
            and FileExists(ExpandConstant('{app}\vendor\win\zadig.exe'));
end;

function UseZadigAndNotUpdate: Boolean;
begin
  // UseZadig() harus sudah dideklarasikan di atas: Pascal Script tidak
  // mengenal pemanggilan fungsi yang baru muncul belakangan.
  Result := UseZadig() and (not IsUpdateMode());
end;

// Catatan: JANGAN memeriksa folder 'dist' di sini.
// Setup.exe yang sudah didistribusikan berada di folder unduhan pengguna,
// sehingga pemeriksaan terhadap folder build akan selalu gagal dan menghalangi
// pemasangan. Kelengkapan file build sudah diperiksa saat compile ([Files]).

// --- mode pembaruan ---------------------------------------------------------
// Aplikasi mengunduh zip rilis baru, lalu menjalankan installer ini dengan
//   YurLaps-Setup.exe /VERYSILENT /UPDATEZIP=<file.zip>
// Installer tetap memasang seluruh paket (pintasan, uninstaller, entri
// "Apps & features" selalu sinkron), lalu menimpa isinya dengan isi zip.
procedure ApplyUpdateZip();
var
  rc: Integer;
  tmp, base, zipFile, psFile: String;
begin
  zipFile := UpdateZip();
  if not FileExists(zipFile) then
  begin
    Log('Zip pembaruan tidak ditemukan: ' + zipFile);
    exit;
  end;
  tmp  := ExpandConstant('{tmp}\yurlaps-update');
  base := ExpandConstant('{app}');
  ForceDirectories(tmp);
  // Skrip PowerShell ditulis ke file dulu lalu dijalankan dengan -File.
  // Satu baris perintah penuh tanda kutip bertumpuk terlalu mudah salah.
  psFile := tmp + '\extract.ps1';
  SaveStringToFile(psFile,
    'Add-Type -AssemblyName System.IO.Compression.FileSystem' + #13#10 +
    '[System.IO.Compression.ZipFile]::ExtractToDirectory(' +
    PsQuote(zipFile) + ', ' + PsQuote(tmp) + ')' + #13#10, False);
  Exec('powershell', '-NoProfile -ExecutionPolicy Bypass -File "' + psFile + '"',
       '', SW_HIDE, ewWaitUntilTerminate, rc);
  // robocopy ada di semua Windows 8/8.1/10/11; data pengguna tidak disentuh
  Exec('robocopy', '"' + tmp + '" "' + base + '" /E /R:2 /W:1 /NFL /NDL /NJH /NJS ' +
       '/XD "' + base + '\data" /XF portable.txt', '', SW_HIDE,
       ewWaitUntilTerminate, rc);
  DelTree(tmp, True, True, True);
  Log('Isi zip pembaruan dipasang (kode ' + IntToStr(rc) + ')');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    Log('YurLaps ' + '{#AppVersion}' + ' terpasang di ' + ExpandConstant('{app}'));
    if IsUpdateMode() then
      ApplyUpdateZip();
  end;
end;
