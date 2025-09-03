import mysql.connector
import os

class Database:
    def __init__(self):
        self.connection = None
