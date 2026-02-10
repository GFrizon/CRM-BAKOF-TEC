' Roda oculto com pythonw.exe e grava logs
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\AppMonitoramento\Monitoramento Clientes Bakof"
cmd = """" & "C:\AppMonitoramento\Monitoramento Clientes Bakof\.venv\Scripts\pythonw.exe" & """" & " " & _
      """" & "C:\AppMonitoramento\Monitoramento Clientes Bakof\app.py" & """" & " " & _
      "1>>" & """" & "C:\AppMonitoramento\Monitoramento Clientes Bakof\logs\ligacoes.out.log" & """" & " " & _
      "2>>" & """" & "C:\AppMonitoramento\Monitoramento Clientes Bakof\logs\ligacoes.err.log" & """"
sh.Run cmd, 0, False
