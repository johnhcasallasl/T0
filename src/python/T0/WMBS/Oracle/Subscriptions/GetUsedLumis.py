"""
_GetUsedLumis_

Oracle implementation of GetUsedLumis

Returns the already used lumis for a given subscription
"""

from WMCore.Database.DBFormatter import DBFormatter

class GetUsedLumis(DBFormatter):

    def execute(self, subscription, conn = None, transaction = False):

        # check which lumis are already in acquired, complete
        # or failed files for this subscription

        lumiSet = set()

        sql = """SELECT wmbs_file_runlumi_map.lumi AS lumi
                 FROM wmbs_sub_files_acquired
                 INNER JOIN wmbs_file_runlumi_map ON
                   wmbs_file_runlumi_map.fileid = wmbs_sub_files_acquired.fileid
                 WHERE wmbs_sub_files_acquired.subscription = :subscription
                 """

        results = self.dbi.processData(sql, { 'subscription' : subscription },
                                       conn = conn, transaction = transaction)

        for result in self.formatDict(results):
            lumiSet.add(result['lumi'])

        sql = """SELECT wmbs_file_runlumi_map.lumi AS lumi
                 FROM wmbs_sub_files_complete
                 INNER JOIN wmbs_file_runlumi_map ON
                   wmbs_file_runlumi_map.fileid = wmbs_sub_files_complete.fileid
                 WHERE wmbs_sub_files_complete.subscription = :subscription
                 """

        results = self.dbi.processData(sql, { 'subscription' : subscription },
                                       conn = conn, transaction = transaction)

        for result in self.formatDict(results):
            lumiSet.add(result['lumi'])

        sql = """SELECT wmbs_file_runlumi_map.lumi AS lumi
                 FROM wmbs_sub_files_failed
                 INNER JOIN wmbs_file_runlumi_map ON
                   wmbs_file_runlumi_map.fileid = wmbs_sub_files_failed.fileid
                 WHERE wmbs_sub_files_failed.subscription = :subscription
                 """

        results = self.dbi.processData(sql, { 'subscription' : subscription },
                                       conn = conn, transaction = transaction)

        for result in self.formatDict(results):
            lumiSet.add(result['lumi'])

        return lumiSet
