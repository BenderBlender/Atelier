Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv\Scripts\pythonw.exe"
If Not fs.FileExists(python) Then
    MsgBox "Python environment missing. See README.md.", 48, "Atelier"
Else
    shell.CurrentDirectory = root
    shell.Run """" & python & """ """ & root & "\main.py""", 1, False
End If
