; Installateur « un clic » de PDF Facile (Inno Setup 6)
; Compilé automatiquement par build.bat — produit dist\Installer-PDF-Facile.exe

#ifndef AppVersion
  #define AppVersion "1.1"
#endif

[Setup]
AppId={{8C3E6A52-4B1F-4E0A-9F5D-2A7C1B9E4D11}
AppName=PDF Facile
AppVersion={#AppVersion}
AppVerName=PDF Facile {#AppVersion}
AppPublisher=Sébastien Védrine
AppPublisherURL=https://github.com/sebastien-vedrine/PDF-Facile
AppSupportURL=https://github.com/sebastien-vedrine/PDF-Facile/issues
AppUpdatesURL=https://github.com/sebastien-vedrine/PDF-Facile/releases
DefaultDirName={localappdata}\Programs\PDF Facile
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Installer-PDF-Facile
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PDF Facile.exe
UninstallDisplayName=PDF Facile
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ChangesAssociations=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
fr.WelcomeLabel2=Ce programme va installer PDF Facile sur votre ordinateur.%n%nPDF Facile permet de lire, remplir, signer et modifier vos documents PDF, simplement.%n%nCliquez sur « Suivant » pour continuer.

[Tasks]
Name: "desktopicon"; Description: "Mettre une icône PDF Facile sur le Bureau"

[InstallDelete]
; mise à jour propre : on retire les fichiers de l'ancienne version
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\PDF Facile\*"; DestDir: "{app}"; Excludes: "install.bat,uninstall.bat"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\PDF Facile"; Filename: "{app}\PDF Facile.exe"
Name: "{userdesktop}\PDF Facile"; Filename: "{app}\PDF Facile.exe"; Tasks: desktopicon

[Registry]
; « Ouvrir avec… » pour les fichiers PDF (utilisateur courant, sans droits administrateur)
Root: HKCU; Subkey: "Software\Classes\PDFFacile.Document"; ValueType: string; ValueData: "Document PDF"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PDFFacile.Document\DefaultIcon"; ValueType: string; ValueData: """{app}\PDF Facile.exe"",0"
Root: HKCU; Subkey: "Software\Classes\PDFFacile.Document\shell\open\command"; ValueType: string; ValueData: """{app}\PDF Facile.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "PDFFacile.Document"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Facile.exe"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Facile.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\PDF Facile.exe\shell\open\command"; ValueType: string; ValueData: """{app}\PDF Facile.exe"" ""%1"""

[Run]
Filename: "{app}\PDF Facile.exe"; Description: "Ouvrir PDF Facile maintenant"; Flags: nowait postinstall skipifsilent
