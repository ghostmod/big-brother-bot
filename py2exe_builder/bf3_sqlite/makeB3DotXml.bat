@echo creating b3.xml ...
sed.exe -e "s/{public_ip}/%1/" -e "s/{port}/%2/" -e "s/{rcon_ip}/%1/" -e "s/{rcon_port}/%2/" -e "s/{rcon_password}/%3/" "%~dp0\template_b3.xml" > "%~dp0\b3.xml"