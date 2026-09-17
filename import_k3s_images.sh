#!/bin/bash
# Importa as imagens locais do LFT para o containerd do K3s.
# Rode depois de construir as imagens (README, seção 2.1) e de instalar o K3s.

if ! command -v k3s >/dev/null 2>&1; then
    echo "K3s não encontrado. Este script só é necessário para o backend k3s."
    exit 0
fi

for img in quagga lft-iperf:latest neubot/dash-client:latest; do
    if ! sudo docker image inspect "$img" >/dev/null 2>&1; then
        echo "ERRO: imagem '$img' não encontrada. Construa as imagens primeiro (README 2.1)."
        exit 1
    fi
    echo "Importando $img..."
    sudo docker save "$img" | sudo k3s ctr images import -
done

# Imagens públicas (ONOS): o containerd do K3s consegue baixá-las sozinho na
# primeira execução, mas um pull a frio de uma imagem de 500MB+ pode passar
# do timeout de criação do pod. Se já estiverem no Docker local (comum, já
# que os testes em modo docker as baixam), importar daqui é instantâneo e
# evita esse timeout; se não estiverem, pula sem erro - o containerd ainda
# vai baixá-las sozinho, só que mais devagar no primeiro uso.
for img in onosproject/onos:1.6 onosproject/onos:2.5.0; do
    if sudo docker image inspect "$img" >/dev/null 2>&1; then
        echo "Importando $img..."
        sudo docker save "$img" | sudo k3s ctr images import -
    else
        echo "Aviso: imagem '$img' não está no Docker local, pulando (containerd vai baixá-la sozinho quando precisar)."
    fi
done

echo "Pronto."
