# check_httpd

foo

Simple service to check the availability of an http interface. The service is running
as a daemon and exporting the data as Prometheus metric. In case of error, it will
log details of the error to stdout (eg. response body for 500 error).

status: early alpha ;)

## ec2 deploy

```bash
sudo apt update
sudo apt install -y python3-venv

git clone git@github.com:zarnovican/check_httpd.git
cd check_httpd
mkdir ~/.virtualenvs/
python3 -m venv ~/.virtualenvs/check_httpd
~/.virtualenvs/check_httpd/bin/pip install -r requirements.txt

vim /etc/systemd/system/check_httpd.service
sudo systemctl daemon-reload
sudo systemctl enable check_httpd
sudo systemctl start check_httpd
```

`check_httpd.service`:
```
[Unit]
Description=Check httpd

[Service]
Environment=URLS=https://api.example.com/eu1/api/ping,https://api.example.com/us1/api/ping
ExecStart=/home/ubuntu/.virtualenvs/check_httpd/bin/python /home/ubuntu/check_httpd/check_httpd.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

## ec2 deploy - dummy vmagent

```bash
sudo apt update
sudo apt install -y victoria-metrics

vim ~/prometheus.yaml
sudo vim /etc/systemd/system/vmagent.service
sudo systemctl daemon-reload
sudo systemctl enable vmagent
sudo systemctl start vmagent
```

prometheus.yaml:
```yaml
global:
  scrape_interval: 10s

  external_labels:
    region: eu1

scrape_configs:
  - job_name: check_httpd
    metrics_path: '/metrics'
    static_configs:
      - targets: ['localhost:8000']
```

vmagent.service:
```
[Unit]
Description=vmagent

[Service]
ExecStart=/usr/bin/vmagent --promscrape.config=/home/ubuntu/prometheus.yaml --remoteWrite.url=http://victoriametrics.example.com:8428/api/v1/write
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
```
