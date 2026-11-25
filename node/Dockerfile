# Imagem única para servidores VPN e clientes (máquinas virtuais do laboratório)
FROM alpine:3.20
# wireguard-go não existe no Alpine: o túnel usa o WireGuard do kernel (Linux 5.6+ e WSL2 já trazem)
RUN apk add --no-cache python3 iproute2 iproute2-tc wireguard-tools iperf3 tcpdump \
    && mkdir -p /etc/vpnlab /run/vpnlab
COPY agent.py /app/agent.py
EXPOSE 9100 9000/udp 51820/udp 51821/udp
CMD ["python3", "-u", "/app/agent.py"]
