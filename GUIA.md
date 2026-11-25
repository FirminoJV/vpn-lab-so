# Guia do grupo — do zero até a medição

Este guia é para os três integrantes. Siga na ordem. Tempo total: ~40 min na primeira vez
(a maior parte é instalação e download), depois ~15 min por medição.

| Integrante | Frente | Comando da medição | Duração |
|---|---|---|---|
| João Vitor | Perda de pacotes (%) | `./lab medir perda` | ~12 min |
| Pedro Nicoletti | Latência RTT (ms) | `./lab medir latencia` | ~12 min |
| Renan Santos | Vazão por usuário (Mbit/s) | `./lab medir vazao` | ~9 min |

---

## Passo 1 — Preparar o computador

**Precisa de:** 4 núcleos e 8 GB de RAM no mínimo, 15 GB livres, internet (só para instalar).

- **Windows 10/11 → use o WSL2** (Ubuntu dentro do Windows, gratuito). Siga a seção 1A.
- **Ubuntu 22.04/24.04 → siga direto para o Passo 2.**
- **Mac → crie uma VM Ubuntu no [UTM](https://mac.getutm.app/)** (4 CPUs, 8 GB) e siga dentro dela.

### 1A. Windows: instalar o Ubuntu no WSL2

1. Abra o **PowerShell como administrador** (menu Iniciar → digite PowerShell → botão direito → Executar como administrador) e rode:
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
2. **Reinicie o computador** se ele pedir.
3. Depois de reiniciar, a janela do Ubuntu abre sozinha (ou procure "Ubuntu" no menu Iniciar). Crie um usuário e uma senha.
   *A senha não aparece enquanto você digita, isso é normal.*
4. Volte ao PowerShell (normal) e garanta o WSL atualizado:
   ```powershell
   wsl --update
   wsl --shutdown
   ```
5. Abra o Ubuntu de novo. **Daqui para frente, todos os comandos são digitados na janela do Ubuntu**, não no PowerShell.

> Não instale o Docker Desktop. O Docker vai ser instalado dentro do Ubuntu (Passo 2).
> Se o Docker Desktop já estiver instalado, abra-o em Settings → Resources → WSL integration, **desmarque o Ubuntu**, aplique e feche o Docker Desktop.

## Passo 2 — Instalar o Docker (uma vez só)

Na janela do Ubuntu, rode:

```bash
sudo apt update && sudo apt install -y curl unzip
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
sudo apt install -y linux-modules-extra-$(uname -r) 2>/dev/null || true
```

Depois **feche a janela do Ubuntu e abra de novo** (no Windows) ou faça logout/login (no Ubuntu), para o Docker funcionar sem `sudo`. Confira:

```bash
docker run --rm hello-world
```

Se aparecer "Hello from Docker!", está pronto.

> **Windows:** se aparecer "Cannot connect to the Docker daemon", rode `sudo service docker start` e tente de novo.

## Passo 3 — Pegar o projeto

Coloque o arquivo `vpn-lab.zip` na sua pasta pessoal e rode:

```bash
cd ~
# Windows (WSL2): copie o zip da pasta Downloads do Windows para dentro do Ubuntu
cp /mnt/c/Users/*/Downloads/vpn-lab.zip ~/ 2>/dev/null; ls ~/vpn-lab.zip
unzip vpn-lab.zip
cd vpn-lab
chmod +x lab
```

> Descompacte **dentro do Ubuntu** (com `unzip`), não pelo Explorador do Windows: assim os scripts continuam executáveis.

**Regra do grupo:** ninguém edita o `topology.toml`. Todos precisam da mesma "internet" simulada
para os resultados serem comparáveis.

## Passo 4 — Subir o laboratório

```bash
./lab up
```

Na primeira vez ele baixa e monta as imagens (5–10 min). No fim, deve aparecer:

- `emulação de rede (netem) funcionando`
- uma tabela com 24 máquinas, todas com **emulação `ativa`** e túneis e handshakes completos (ex.: `6/6`)

Abra o painel no navegador: **http://localhost:3000** → menu Dashboards → pasta **VPN Lab**.

## Passo 5 — Conferir que está tudo certo (2 min)

Espere 1 minuto depois do `up` e rode:

```bash
./lab matrix
```

Confira três valores da tabela (RTT pelo túnel). Uma diferença de 1–3 ms é normal:

| Linha (usuário) | Coluna (servidor) | Valor esperado |
|---|---|---|
| cli-sao-1 | srv-fra | ~215 ms |
| cli-fra-1 | srv-fra | ~10 ms |
| cli-tok-1 | srv-jnb | ~372 ms |

**Se tudo estiver perto de 1 ms**, a emulação não está ativa. Veja "Problemas comuns" no fim deste guia.

Depois registre a sua máquina (vai no campo **Dispositivo** do trabalho):

```bash
./lab env
```

## Passo 6 — Fazer a sua medição

Antes de começar:
- notebook **na tomada**;
- feche navegador pesado, jogos e chamadas de vídeo;
- **não use o computador** durante a medição.

Rode **só o comando da sua frente** (veja a tabela no topo):

```bash
./lab reset              # garante que não há falhas injetadas
./lab medir perda        # João
./lab medir latencia     # Pedro
./lab medir vazao        # Renan
```

O que o comando faz sozinho, igual para todos:

- **Condições:** 0, 2, 8 e 18 usuários usando o mesmo servidor VPN ao mesmo tempo.
- **Repetições:** 3 por condição. A ordem alterna (crescente/decrescente) para não enviesar.
- **Duração:** 40 s por condição, com 10 s de pausa entre elas.
- **Carga:**
  - latência e vazão: download TCP no `srv-fra`;
  - perda: UDP a 8 Mbit/s por usuário no `srv-jnb`.
- **Resultado:** imprime um resumo com média, desvio, mínimo, máximo e variação % em relação à condição base.

Durante a medição, acompanhe no Grafana o dashboard **4. Medição do grupo**:
- no topo, escolha o servidor: **srv-jnb** para a perda (João) ou **srv-fra** para latência e vazão (Pedro e Renan);
- no canto superior direito, escolha o período (ex.: "Last 30 minutes");
- cada condição aparece como uma faixa laranja nos gráficos.

Ao terminar, tire um print desse dashboard para o trabalho: ele mostra os usuários subindo e a sua métrica reagindo, no mesmo eixo de tempo.

Quer testar antes de valer? `./lab medir <frente> --rapido` faz uma versão de 1 minuto.
Depois rode o comando completo.

## Passo 7 — Enviar os resultados para o grupo

Na pasta `results/` aparecem os seus arquivos:

```
medicao-<frente>-<data>.csv          todas as repetições
medicao-<frente>-<data>-resumo.csv   média por condição (é o que vai no gráfico)
medicao-<frente>-<data>.json         tudo + dados da máquina
ambiente-<seu-pc>-<data>.txt         descrição da máquina
```

**Pedro e Renan:** mandem esses 4 arquivos para o **João**.

**João:** coloque os arquivos recebidos na sua pasta `results/` e rode:

```bash
./lab report
```

Isso gera `results/relatorio-mais-recente.html` (abra no navegador). Ele traz:
- o gráfico combinado do trabalho: eixo X = usuários simultâneos, eixo Y = variação %, uma série por frente;
- um gráfico e uma tabela para cada frente.

Para gerar PDF, use Ctrl+P → Salvar como PDF.

## Passo 8 — Desligar

```bash
./lab down          # para tudo, mantém o histórico
./lab down --all    # para e apaga o histórico do Prometheus
```

Para voltar depois: `./lab up`.

---

## Problemas comuns

| Sintoma | Solução |
|---|---|
| Windows: `Cannot connect to the Docker daemon` | Rode `sudo service docker start`. Se o erro continuar depois de reabrir o Ubuntu, rode `echo -e "[boot]\nsystemd=true" \| sudo tee /etc/wsl.conf`, depois `wsl --shutdown` no PowerShell e abra o Ubuntu de novo. |
| Windows: o Grafana não abre em localhost:3000 | Confira se o `./lab up` terminou. Em último caso, rode `hostname -I` no Ubuntu e abra `http://<primeiro-ip>:3000` no navegador do Windows. |
| Windows: pouca memória ou travando | Crie o arquivo `C:\Users\<seu-usuário>\.wslconfig` com as linhas `[wsl2]`, `memory=6GB` e `processors=4`, rode `wsl --shutdown` e abra o Ubuntu de novo. |
| `permission denied` ao usar o docker | Você não saiu e entrou de novo depois do passo 2. Faça logout/login. |
| `./lab: Permission denied` | Rode `chmod +x lab`. |
| "o kernel NÃO aceitou o netem" ou valores ~1 ms no `./lab matrix` | Rode `sudo apt install linux-modules-extra-$(uname -r)` e `sudo modprobe -a sch_netem sch_htb`, depois `./lab down && ./lab up`. |
| Aviso "menos de 4 núcleos" | Aumente as CPUs da VM para 4 (com a VM desligada). |
| `túneis ok` incompleto no `./lab status` | Espere 30 s e rode de novo. Se continuar, rode `./lab logs cli-sao-1` e mande a saída no grupo. |
| Grafana vazio | Espere 2 min. Confira se todos os alvos estão "UP" em http://localhost:9090/targets. |
| Porta 3000 ou 9090 já em uso | Troque `grafana_port` ou `prometheus_port` no `topology.toml` (é a única edição permitida nele). |
| Medição com `sem dado` | O laboratório ainda estava subindo. Espere 2 min depois do `up` e rode de novo. |

## Comandos úteis

```bash
./lab status               # estado das 24 máquinas
./lab matrix --metric loss # perda sem carga, usuário x servidor
./lab optimize             # melhor servidor intermediário para todos
./lab shell cli-sao-1      # terminal dentro de uma máquina virtual
./lab help                 # lista completa
```
