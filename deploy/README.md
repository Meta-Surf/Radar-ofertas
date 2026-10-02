# Implantação na VPS

Os arquivos desta pasta reproduzem a configuração operacional atualmente usada em produção, sem incluir segredos.

## Pré-requisitos

- projeto em `/opt/radar`;
- usuário/grupo `radar`;
- virtualenv em `/opt/radar/.venv`;
- `.env` local configurado e não versionado.

## Systemd

Copie os units de `deploy/systemd/` para `/etc/systemd/system/`, depois execute:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now radar-monitor.service
sudo systemctl enable --now radar-publicador.service
sudo systemctl enable --now radar-shopee.service
sudo systemctl enable --now radar-backup.timer
```

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

## Verificação

```bash
systemctl is-active radar-monitor.service radar-publicador.service radar-shopee.service
systemctl is-active radar-backup.timer
cd /opt/radar
./.venv/bin/python configuracao.py
./.venv/bin/python radar_health.py
```
