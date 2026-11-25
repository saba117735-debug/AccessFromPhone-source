; Inno Setup script: turns dist\AccessFromPhone\ into one Setup.exe.  Built by build.ps1 (iscc /DAppVersion=x.y.z installer.iss)
#define AppName "AccessFromPhone"
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#define AppExe "AccessFromPhone.exe"

[Setup]
; A fixed id, so a new version replaces the old one instead of installing beside it.
AppId={{7C1F3A6E-4B52-4D0B-9E57-3B2A91C4D8F1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AccessFromPhone
; Per-user: no administrator prompt, installs to %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=AccessFromPhone-Setup-{#AppVersion}
SetupIconFile=build\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=build\wizard-left-164.bmp,build\wizard-left-246.bmp,build\wizard-left-328.bmp
WizardSmallImageFile=build\wizard-small-55.bmp,build\wizard-small-83.bmp,build\wizard-small-110.bmp
LicenseFile=License.txt
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupMutex=AccessFromPhoneSetupMutex
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Welcome to {#AppName}
WelcomeLabel2=This sets up {#AppName} on this PC, so you can ask it to find, read and change files from your phone. You approve every change, and can undo it.%n%nIt takes about a minute.
FinishedHeadingLabel=You're all set
FinishedLabelNoIcons={#AppName} is installed.%n%nOpen it, sign in with your Windows password, then scan the QR code with your phone.
FinishedLabel={#AppName} is installed.%n%nOpen it, sign in with your Windows password, then scan the QR code with your phone.

[Tasks]
Name: "autostart"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "While you use this PC:"
Name: "desktopicon"; Description: "Add a shortcut to the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\AccessFromPhone\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Start with Windows": it signs in quietly to the tray, with no window.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
// A running copy would block replacing its files (an update, or a reinstall): stop it first.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#AppExe} /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

// Uninstalling removes the program, never the person's data, unless they say so.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\AccessFromPhone');
    if (not UninstallSilent) and DirExists(Data) then
      if MsgBox('Also delete your {#AppName} settings, chats and paired phones?' + #13#10 + #13#10 +
                'Choose No to keep them in case you install it again.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
