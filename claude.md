# LFT: suporte a Docker e K3s

O LFT é um emulador de redes que roda sobre Docker em host único. Estamos
acrescentando suporte a K3s em host único, preservando o comportamento atual.

## Regra central

A refatoração troca quem executa o comando, não o que o comando faz.
Em Docker, cada linha convertida deve gerar uma string idêntica à que gera hoje.

## Como converter

Substitua o trecho `docker exec {nome}` por `{backend.exec_prefix(nome)}`.
Deixe o resto da linha intacto: a função de subprocess usada, `check`, `shell`,
`stdout`, aspas escapadas, tudo.

    # antes
    subprocess.run(f"docker exec {sw} ovs-vsctl add-br {sw}", shell=True)
    # depois
    subprocess.run(f"{backend.exec_prefix(sw)} ovs-vsctl add-br {sw}", shell=True)

`Node.run()` não serve como substituto: ele acrescenta `bash -c`, usa `Popen` em
vez de `subprocess.run`, e captura stdout.

## Bugs conhecidos que ficam como estão

Corrigi-los mudaria o comportamento e invalidaria a comparação entre cenários.
Aponte no relatório final se encontrar outros; não corrija.

- `Router.setInterfaceProperties` ignora o parâmetro `jitter`
- `Router` usa `tc qdisc replace`, `Node` usa `add`
- `__interfaceExists` usa `grep`, então `s1` casa com `s10`
- `connectToInternet` deixa regras de iptables no host
- `netem` é aplicado em uma ponta só do enlace
- `utils.py:44` tem `172.17.0.2` hardcoded

## Arquitetura

1. Scripts de topologia (`constants.py`, `main.py`) — não mudam
2. `node.py`, `switch.py`, `controller.py` — lógica de emulação, delegam ao backend
3. `backends/docker.py` e `backends/k3s.py` — ciclo de vida de container

Rede (`ip link`, `ip -n`, `tc netem`, `ovs-vsctl`) é kernel, idêntica nos dois
substratos, e fica fora do backend.

`docker/` contém os Dockerfiles e não é tocado.

## Escopo de cada tratamento

Os dois diretórios recebem tratamentos diferentes:

- `profissa_lft/node.py`, `switch.py`, `controller.py` — o código Docker vira
  métodos de `backends/docker.py`: create_node, delete_node, node_pid,
  copy_to_node, enable_namespace. Não são trocas de prefixo; são comandos
  diferentes (`docker run`, `docker cp`, `docker inspect`, `docker kill`),
  que não têm equivalente por prefixo.

- `onos_topologies/` — troca de prefixo, conforme a regra acima.

## Verificação

Cada prompt de implementação escreve testes unitários descartáveis para validar
o que acabou de ser feito, em `tests/tmp_<assunto>.py`. Eles não fazem parte da
entrega — servem para confirmar a etapa e são removidos ao final.

A validação real do projeto é o teste de funcionamento completo, no último
prompt: as topologias DASH e iperf rodando de ponta a ponta nos dois cenários.