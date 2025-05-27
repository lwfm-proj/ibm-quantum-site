"""
IBM Quantum Site to run in a virual environment with its own set of dependencies, 
independent of the global environment.
"""

#pylint: disable = invalid-name

from lwfm.base.Site import SiteAuth, SiteRun, SiteRepo, SiteSpin
from lwfm.sites.VenvSite import VenvSite

from ibm_quantum_site.IBMQuantumSite import IBMQuantumSite
from ibm_quantum_site.IBMQuantumSite import main, poll_job

class IBMQuantumVenvSite(VenvSite):
    """
    A Site driver for running IBM quantum jobs in a virtual environment.
    """
    SITE_NAME = "ibm-quantum-venv"

    def __init__(self, site_name: str = None,
                 authDriver: SiteAuth = None,
                 runDriver:  SiteRun = None,
                 repoDriver: SiteRepo = None,
                 spinDriver: SiteSpin = None,
                 ) -> None:
        self.localSite = IBMQuantumSite()
        self.localSite.setSiteName(site_name or self.SITE_NAME)
        self.setSiteName(site_name or self.SITE_NAME)
        self._realAuthDriver = authDriver or self.localSite.getAuthDriver()
        self._realRunDriver = runDriver or self.localSite.getRunDriver()
        self._realRepoDriver = repoDriver or self.localSite.getRepoDriver()
        self._realSpinDriver = spinDriver or self.localSite.getSpinDriver()
        super().__init__(
            self.getSiteName(),
            self._realAuthDriver,
            self._realRunDriver,
            self._realRepoDriver,
            self._realSpinDriver
            )


#**********************************************************************************
# Main - testing only

if __name__ == "__main__":
    import sys

    # Check if an argument was provided (job ID)
    if len(sys.argv) > 1:
        job_id = sys.argv[1]
        print(f"Job ID provided: {job_id}")
        # Handle job ID here
        poll_job(job_id, IBMQuantumVenvSite.SITE_NAME)
    else:
        # No arguments, run the main function
        main(IBMQuantumVenvSite.SITE_NAME)
