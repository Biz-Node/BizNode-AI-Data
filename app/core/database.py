import os
from neo4j import GraphDatabase

class Neo4jClient:
    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.database = os.getenv("NEO4J_DATABASE")
        self._driver = None

    def connect(self):
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password), database=self.database)

    def close(self):
        if self._driver:
            self._driver.close()

    @property
    def driver(self):
        return self._driver

neo4j_client = Neo4jClient()
