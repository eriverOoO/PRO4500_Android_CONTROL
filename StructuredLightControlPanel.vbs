Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
app = root & "\StructuredLightControlPanel.exe"

If Not fso.FileExists(app) Then
  MsgBox "Control panel app was not found:" & vbCrLf & app, vbCritical, "Structured Light Controller"
  WScript.Quit 1
End If

shell.Run """" & app & """", 0, False
