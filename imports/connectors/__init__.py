"""
Connectors layer — adaptateurs normalisés vers une structure pivot :
    { 'headers': [...], 'rows': [ {col: val, ...}, ... ] }
"""
from .excel import ExcelConnector
from .csv_connector import CSVConnector
from .word import WordConnector
from .mysql_connector import MySQLConnector
from .api import APIConnector

CONNECTORS = {
    'excel': ExcelConnector,
    'csv':   CSVConnector,
    'word':  WordConnector,
    'mysql': MySQLConnector,
    'api':   APIConnector,
}


def get_connector(source_type):
    cls = CONNECTORS.get(source_type)
    if not cls:
        raise ValueError(f"Type de source inconnu : {source_type}")
    return cls
