# Ponto de entrada simples da aplicacao.
# Toda a inicializacao real do servidor fica encapsulada no backend.
from backend.server import run


if __name__ == "__main__":
    # Inicia o servidor HTTP e o monitor em background.
    run()
