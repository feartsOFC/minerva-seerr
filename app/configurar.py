"""Gera um link de configuração de uso único (chaves do IGDB, senhas e, numa instalação nova, o 1º admin).

Uso: python -m app.configurar [minutos]      (padrão 60; o endereço vem de MINERVA_URL)
"""
import sys

from .main import gerar_link_config

if __name__ == "__main__":
    print(gerar_link_config(int(sys.argv[1]) if len(sys.argv) > 1 else 60))
