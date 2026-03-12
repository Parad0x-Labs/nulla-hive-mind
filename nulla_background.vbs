Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir

Set env = shell.Environment("Process")
env("PYTHONPATH") = scriptDir
If env("NULLA_HOME") = "" Then
  env("NULLA_HOME") = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.nulla_runtime"
End If

pythonwPath = scriptDir & "\.venv\Scripts\pythonw.exe"
cmd = """" & pythonwPath & """ -m apps.nulla_api_server"
shell.Run cmd, 0, False

