# Implantação na VPS

Os arquivos desta pasta reproduzem a configuração operacional atualmente usada em produção, sem incluir segredos.

## Pré-requisitos

- projeto em `/opt/radar`;
- usuário/grupo `radar`;
- virtualenv em `/opt/radar/.venv`;
- `.env` local configurado e não versionado.

## Dependências

Na VPS, instale o conjunto exato validado pelo projeto:

```bash
/opt/radar/.venv/bin/python -m pip install -r /opt/radar/requirements.lock
/opt/radar/.venv/bin/python -m pip check
```

`requirements.txt` permanece como especificação de faixas para manutenção; `requirements.lock` é a referência reprodutível para produção e CI.

## Systemd

Copie os units de `deploy/systemd/` para `/etc/systemd/system/`, depois execute:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now radar-monitor.service
sudo systemctl enable --now radar-publicador.service
sudo systemctl enable --now radar-shopee.service
sudo systemctl enable --now radar-backup.timer
sudo systemctl enable --now radar-health.timer
```

Os três serviços principais usam `UMask=0077`, de modo que novos arquivos operacionais sejam privados por padrão. O código de produção também aplica essa máscara no processo para manter a proteção mesmo antes de uma reinstalação dos units.

## Backup

O script versionado em `deploy/bin/radar-backup.sh` deve ser instalado como:

```bash
sudo install -o root -g radar -m 750 deploy/bin/radar-backup.sh /usr/local/bin/radar-backup.sh
```

Ele inclui:

- `.env`;
- `publicacoes.sqlite3`;
- `kabum_historico.sqlite3`;
- `monitor_ofertas.session`;
- `monitor_recuperacao.json`;
- `destinos_confirmados.json`, quando existir;
- caches/feed KaBuM relevantes;
- `imagens_ofertas/`.

Filas JSONL antigas não fazem parte do backup ativo porque foram substituídas pelo SQLite.

### Cópia externa criptografada

`deploy/radar-backup-export.sh` exporta o backup mais recente somente após criptografia GPG. Configure `RADAR_BACKUP_GPG_RECIPIENT` com uma chave pública disponível na VPS e `RADAR_BACKUP_EXTERNAL_DIR` com um volume/diretório externo montado. A chave privada não deve ficar na VPS.

O exportador falha fechado se destinatário ou destino externo não estiverem configurados. A retenção padrão dos arquivos criptografados no destino é de 30 dias.

## Verificação

```bash
systemctl is-active radar-monitor.service radar-publicador.service radar-shopee.service
systemctl is-active radar-backup.timer
systemctl is-active radar-health.timer
cd /opt/radar
./.venv/bin/python configuracao.py
./.venv/bin/python radar_health.py
```
