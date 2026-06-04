import contextlib
import ibis
import duckdb
from tenacity import retry, retry_if_exception, wait_exponential


class ResultsStorage:
    _SCHEMA = {
        "basename": "string",
        "name": "string",
        "sysenv": "string",
        "job_submit_timestamp": "timestamp",
        "job_completion_timestamp": "timestamp",
        "job_elapsed_time_secs": "float64",
        "job_nodelist_folded": "string",
        "result": "string",
        "pvar": "string",
        "pval": "float64",
        "punit": "string",
        "pref": "float64",
        "plower": "float64",
        "pupper": "float64",
        "presult": "string",
        "sdata": "json",
        "rdata": "json",
        "tdata": "json",
        "rindex": "int64",
        "tindex": "int64",
    }

    @staticmethod
    def _is_locked_error(exc):
        match ibis.get_backend().name:
            case 'duckdb':
                return (isinstance(exc, duckdb.IOException) and
                        'conflicting lock' in str(exc).lower())
            case _:
                return False

    @contextlib.contextmanager
    @retry(
        retry=retry_if_exception(_is_locked_error),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=3),
    )
    def connect(self, resource: str, **kwargs):
        '''Connect to the database.'''
        conn = ibis.connect(resource, **kwargs)
        try:
            yield conn
        finally:
            conn.close()

    def store(self, report: dict) -> None:
        '''Store the report in the database.'''
        raise NotImplementedError
