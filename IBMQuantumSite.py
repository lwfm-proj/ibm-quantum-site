#pylint: disable=invalid-name
"""
lwfm Site driver for IBM Quantum
"""

from typing import List, Union

from qiskit_ibm_runtime import QiskitRuntimeService

from lwfm.base.JobContext import JobContext
from lwfm.base.JobStatus import JobStatus
from lwfm.base.JobDefn import JobDefn
from lwfm.base.Metasheet import Metasheet
from lwfm.base.Workflow import Workflow
from lwfm.base.Site import Site, SiteAuth, SiteRun, SiteRepo, SiteSpin
from lwfm.midware.LwfManager import logger



class IBMQuantumSiteAuth(SiteAuth):
    """
    A Site driver for managing the authentication for an IBM Quantum site
    """
    def login(self, force: bool = False) -> bool:
        """
        Login to the IBM Quantum site
        """
        logger.info("Logging in to IBM Quantum site")
        # do we have a token saved with lwfm?
        token = ""
        service = QiskitRuntimeService(token=token, channel="ibm_cloud", verify=False)
        service.active_account()
        logger.info("Logged in to IBM Quantum site")
        return True

    def isAuthCurrent(self) -> bool:
        """
        Check if the authentication is current
        """
        logger.info("Checking if authentication is current")
        # Implement check logic here


class IBMQuantumSiteRun(SiteRun):
    """
    A Site driver for running jobs on IBM Quantum
    """
    def submit(self, jobDefn: Union['JobDefn', str],
        parentContext: Union[JobContext, Workflow, str] = None,
        computeType: str = None, runArgs: Union[dict, str] = None) -> JobStatus:
        pass

    def getStatus(self, jobId: str) -> JobStatus:
        pass

    def cancel(self, jobContext: Union[JobContext, str]) -> bool:
        pass


class IBMQuantumSiteRepo(SiteRepo):
    """
    A Site driver for managing the repository for an IBM Quantum site
    """
    def put(
        self,
        localPath: str,
        siteObjPath: str,
        jobContext: Union[JobContext, str] = None,
        metasheet: Union[Metasheet, str] = None
    ) -> Metasheet:
        pass

    def get(
        self,
        siteObjPath: str,
        localPath: str,
        jobContext: Union[JobContext, str] = None
    ) -> str:
        pass

    def find(self, queryRegExs: Union[dict, str]) -> List[Metasheet]:
        pass


class IBMQuantumSiteSpin(SiteSpin):
    """
    A Site driver for managing the spin for an IBM Quantum site
    """
    def listComputeTypes(self) -> List[str]:
        """
        List the compute types available on the IBM Quantum site
        """
        logger.info("Listing compute types")
        # Implement list logic here



class IBMQuantumSite(Site):
    """
    A Site driver for IBM Quantum
    """
    SITE_NAME = "ibm-quantum"

    def __init__(self, site_name: str = None,
                 auth_driver: SiteAuth = None,
                 run_driver: SiteRun = None,
                 repo_driver: SiteRepo = None,
                 spin_driver: SiteSpin = None):
        super().__init__(
            site_name or self.SITE_NAME,
            auth_driver or IBMQuantumSiteAuth(),
            run_driver  or IBMQuantumSiteRun(),
            repo_driver or IBMQuantumSiteRepo(),
            spin_driver or IBMQuantumSiteSpin()
        )

def main():
    """
    Main function for testing the IBM Quantum site driver
    """
    site = IBMQuantumSite()
    site.getAuthDriver().login()


if __name__ == "__main__":
    main()
