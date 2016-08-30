"""
_RunConfigAPI_

API for anyting RunConfig related

"""
import logging
import threading
import time

from WMCore.DAOFactory import DAOFactory

from WMCore.WorkQueue.WMBSHelper import WMBSHelper
from WMCore.WMBS.Fileset import Fileset

from T0.RunConfig.Tier0Config import retrieveDatasetConfig
from T0.RunConfig.Tier0Config import addRepackConfig
from T0.RunConfig.Tier0Config import deleteStreamConfig

from T0.WMSpec.StdSpecs.Repack import RepackWorkloadFactory
from T0.WMSpec.StdSpecs.Express import ExpressWorkloadFactory
from WMCore.WMSpec.StdSpecs.PromptReco import PromptRecoWorkloadFactory

def extractConfigParameter(configParameter, era, run):
    """
    _extractConfigParameter_

    Checks if configParameter is era or run dependent. If it is, use the
    provided era and run information to extract the correct parameter.
    """
    if isinstance(configParameter, dict):
        if 'acqEra' in configParameter and era in configParameter['acqEra']:
            return configParameter['acqEra'][era]
        elif 'maxRun' in configParameter:
            newConfigParameter = None
            for maxRun in sorted(configParameter['maxRun'].keys()):
                if run <= maxRun:
                    newConfigParameter = configParameter['maxRun'][maxRun]
            if newConfigParameter:
                return newConfigParameter
        return configParameter['default']
    else:
        return configParameter

def configureRun(tier0Config, run, hltConfig, referenceHltConfig = None):
    """
    _configureRun_

    Called by Tier0Feeder for new runs.

    Retrieve HLT config and configure global run
    settings and stream/dataset/trigger mapping

    """
    logging.debug("configureRun() : %d" % run)
    myThread = threading.currentThread()

    daoFactory = DAOFactory(package = "T0.WMBS",
                            logger = logging,
                            dbinterface = myThread.dbi)

    # dao to update global run settings
    insertStorageNodeDAO = daoFactory(classname = "RunConfig.InsertStorageNode")
    updateRunDAO = daoFactory(classname = "RunConfig.UpdateRun")

    # workaround to make unit test work without HLTConfDatabase
    if hltConfig == None and referenceHltConfig != None:
        hltConfig = referenceHltConfig

    # treat centralDAQ or miniDAQ runs (have an HLT key) different from local runs
    if hltConfig != None:

        # write stream/dataset/trigger mapping
        insertStreamDAO = daoFactory(classname = "RunConfig.InsertStream")
        insertDatasetDAO = daoFactory(classname = "RunConfig.InsertPrimaryDataset")
        insertStreamDatasetDAO = daoFactory(classname = "RunConfig.InsertStreamDataset")
        insertTriggerDAO = daoFactory(classname = "RunConfig.InsertTrigger")
        insertDatasetTriggerDAO = daoFactory(classname = "RunConfig.InsertDatasetTrigger")

        bindsStorageNode = []
        if tier0Config.Global.ExpressSubscribeNode:
            bindsStorageNode.append( { 'NODE' : tier0Config.Global.ExpressSubscribeNode } )

        bindsUpdateRun = { 'RUN' : run,
                           'PROCESS' : hltConfig['process'],
                           'ACQERA' : tier0Config.Global.AcquisitionEra,
                           'BACKFILL' : tier0Config.Global.Backfill,
                           'BULKDATATYPE' : tier0Config.Global.BulkDataType,
                           'EXPRESS_SUBSCRIBE' : tier0Config.Global.ExpressSubscribeNode,
                           'DQMUPLOADURL' : tier0Config.Global.DQMUploadUrl,
                           'AHTIMEOUT' : tier0Config.Global.AlcaHarvestTimeout,
                           'AHDIR' : tier0Config.Global.AlcaHarvestDir,
                           'CONDTIMEOUT' : tier0Config.Global.ConditionUploadTimeout,
                           'DBHOST' : tier0Config.Global.DropboxHost,
                           'VALIDMODE' : tier0Config.Global.ValidationMode }

        bindsStream = []
        bindsDataset = []
        bindsStreamDataset = []
        bindsTrigger = []
        bindsDatasetTrigger = []

        for stream, datasetDict in hltConfig['mapping'].items():
            bindsStream.append( { 'STREAM' : stream } )
            for dataset, paths in datasetDict.items():

                if dataset == "Unassigned path":

                    if run < 240000:
                        pass
                    else:
                        raise RuntimeError("Problem in configureRun() : Unassigned path in HLT menu !")

                else:
                    bindsDataset.append( { 'PRIMDS' : dataset } )
                    bindsStreamDataset.append( { 'RUN' : run,
                                                 'PRIMDS' : dataset,
                                                 'STREAM' : stream } )
                    for path in paths:
                        bindsTrigger.append( { 'TRIG' : path } )
                        bindsDatasetTrigger.append( { 'RUN' : run,
                                                      'TRIG' : path,
                                                      'PRIMDS' : dataset } )

        try:
            myThread.transaction.begin()
            if len(bindsStorageNode) > 0:
                insertStorageNodeDAO.execute(bindsStorageNode, conn = myThread.transaction.conn, transaction = True)
            updateRunDAO.execute(bindsUpdateRun, conn = myThread.transaction.conn, transaction = True)
            insertStreamDAO.execute(bindsStream, conn = myThread.transaction.conn, transaction = True)
            insertDatasetDAO.execute(bindsDataset, conn = myThread.transaction.conn, transaction = True)
            insertStreamDatasetDAO.execute(bindsStreamDataset, conn = myThread.transaction.conn, transaction = True)
            insertTriggerDAO.execute(bindsTrigger, conn = myThread.transaction.conn, transaction = True)
            insertDatasetTriggerDAO.execute(bindsDatasetTrigger, conn = myThread.transaction.conn, transaction = True)
        except Exception as ex:
            logging.exception(ex)
            myThread.transaction.rollback()
            raise RuntimeError("Problem in configureRun() database transaction !")
        else:
            myThread.transaction.commit()

    else:

        bindsUpdateRun = { 'RUN' : run,
                           'PROCESS' : "FakeProcessName",
                           'ACQERA' : "FakeAcquisitionEra",
                           'BACKFILL' : None,
                           'BULKDATATYPE' : "FakeBulkDataType",
                           'AHTIMEOUT' : None,
                           'AHDIR' : None,
                           'CONDTIMEOUT' : None,
                           'DBHOST' : None,
                           'VALIDMODE' : None }

        try:
            myThread.transaction.begin()
            updateRunDAO.execute(bindsUpdateRun, conn = myThread.transaction.conn, transaction = True)
        except Exception as ex:
            logging.exception(ex)
            myThread.transaction.rollback()
            raise RuntimeError("Problem in configureRun() database transaction !")
        else:
            myThread.transaction.commit()

    return

def configureRunStream(tier0Config, run, stream, specDirectory, dqmUploadProxy):
    """
    _configureRunStream_

    Called by Tier0Feeder for new run/streams.

    Retrieve global run settings and build the part
    of the configuration relevant to run/stream
    and write it to the database.

    Create workflows, filesets and subscriptions for
    the processing of runs/streams.

    """
    logging.debug("configureRunStream() : %d , %s" % (run, stream))
    myThread = threading.currentThread()

    daoFactory = DAOFactory(package = "T0.WMBS",
                            logger = logging,
                            dbinterface = myThread.dbi)

    # retrieve some basic run information
    getRunInfoDAO = daoFactory(classname = "RunConfig.GetRunInfo")
    runInfo = getRunInfoDAO.execute(run, transaction = False)[0]

    # treat centralDAQ or miniDAQ runs (have an HLT key) different from local runs
    if runInfo['hltkey'] != None:

        # streams not explicitely configured are repacked
        if stream not in tier0Config.Streams.dictionary_().keys():
            addRepackConfig(tier0Config, stream)

        streamConfig = tier0Config.Streams.dictionary_()[stream]

        # consistency check to make sure stream exists and has datasets defined
        # only run if we don't ignore the stream
        if streamConfig.ProcessingStyle != "Ignore":
            getStreamDatasetsDAO = daoFactory(classname = "RunConfig.GetStreamDatasets")
            datasets = getStreamDatasetsDAO.execute(run, stream, transaction = False)
            if len(datasets) == 0:
                raise RuntimeError("Stream is not defined in HLT menu or has no datasets !")

        # write run/stream processing completion record
        insertRunStreamDoneDAO = daoFactory(classname = "RunConfig.InsertRunStreamDone")

        # write stream/dataset mapping (for special express and error datasets)
        insertDatasetDAO = daoFactory(classname = "RunConfig.InsertPrimaryDataset")
        insertStreamDatasetDAO = daoFactory(classname = "RunConfig.InsertStreamDataset")

        # write stream configuration
        insertCMSSWVersionDAO = daoFactory(classname = "RunConfig.InsertCMSSWVersion")
        insertStreamStyleDAO = daoFactory(classname = "RunConfig.InsertStreamStyle")
        insertRepackConfigDAO = daoFactory(classname = "RunConfig.InsertRepackConfig")
        insertPromptCalibrationDAO = daoFactory(classname = "RunConfig.InsertPromptCalibration")
        insertExpressConfigDAO = daoFactory(classname = "RunConfig.InsertExpressConfig")
        insertSpecialDatasetDAO = daoFactory(classname = "RunConfig.InsertSpecialDataset")
        insertDatasetScenarioDAO = daoFactory(classname = "RunConfig.InsertDatasetScenario")
        insertStreamFilesetDAO = daoFactory(classname = "RunConfig.InsertStreamFileset")
        insertRecoReleaseConfigDAO = daoFactory(classname = "RunConfig.InsertRecoReleaseConfig")
        insertWorkflowMonitoringDAO = daoFactory(classname = "RunConfig.InsertWorkflowMonitoring")
        insertStorageNodeDAO = daoFactory(classname = "RunConfig.InsertStorageNode")
        insertPhEDExConfigDAO = daoFactory(classname = "RunConfig.InsertPhEDExConfig")

        bindsRunStreamDone = {'RUN' : run,
                              'STREAM' : stream}
        bindsCMSSWVersion = []
        bindsDataset = []
        bindsStreamDataset = []
        bindsStreamStyle = {'RUN' : run,
                            'STREAM' : stream,
                            'STYLE': streamConfig.ProcessingStyle }
        bindsRepackConfig = {}
        bindsPromptCalibration = {}
        bindsExpressConfig = {}
        bindsSpecialDataset = {}
        bindsDatasetScenario = []
        bindsStorageNode = []
        bindsPhEDExConfig = []

        #
        # for spec creation, details for all outputs
        #
        outputModuleDetails = []

        #
        # special dataset for some express output
        #
        specialDataset = None

        #
        # for PhEDEx subscription settings
        #
        subscriptions = []

        #
        # first take care of all stream settings
        #
        getStreamOnlineVersionDAO = daoFactory(classname = "RunConfig.GetStreamOnlineVersion")
        onlineVersion = getStreamOnlineVersionDAO.execute(run, stream, transaction = False)

        if streamConfig.ProcessingStyle == "Bulk":

            streamConfig.Repack.CMSSWVersion = streamConfig.VersionOverride.get(onlineVersion, onlineVersion)

            bindsCMSSWVersion.append( { 'VERSION' : streamConfig.Repack.CMSSWVersion } )

            streamConfig.Repack.ScramArch = tier0Config.Global.ScramArches.get(streamConfig.Repack.CMSSWVersion,
                                                                               tier0Config.Global.DefaultScramArch)

            bindsRepackConfig = { 'RUN' : run,
                                  'STREAM' : stream,
                                  'PROC_VER': streamConfig.Repack.ProcessingVersion,
                                  'MAX_SIZE_SINGLE_LUMI' : streamConfig.Repack.MaxSizeSingleLumi,
                                  'MAX_SIZE_MULTI_LUMI' : streamConfig.Repack.MaxSizeMultiLumi,
                                  'MIN_SIZE' : streamConfig.Repack.MinInputSize,
                                  'MAX_SIZE' : streamConfig.Repack.MaxInputSize,
                                  'MAX_EDM_SIZE' : streamConfig.Repack.MaxEdmSize,
                                  'MAX_OVER_SIZE' : streamConfig.Repack.MaxOverSize,
                                  'MAX_EVENTS' : streamConfig.Repack.MaxInputEvents,
                                  'MAX_FILES' : streamConfig.Repack.MaxInputFiles,
                                  'BLOCK_DELAY' : streamConfig.Repack.BlockCloseDelay,
                                  'CMSSW' : streamConfig.Repack.CMSSWVersion,
                                  'SCRAM_ARCH' : streamConfig.Repack.ScramArch }

        elif streamConfig.ProcessingStyle == "Express":

            specialDataset = "Stream%s" % stream
            bindsDataset.append( { 'PRIMDS' : specialDataset } )
            bindsStreamDataset.append( { 'RUN' : run,
                                         'PRIMDS' : specialDataset,
                                         'STREAM' : stream } )
            bindsSpecialDataset = { 'STREAM' : stream,
                                    'PRIMDS' : specialDataset }
            bindsDatasetScenario.append( { 'RUN' : run,
                                           'PRIMDS' : specialDataset,
                                           'SCENARIO' : streamConfig.Express.Scenario } )

            if streamConfig.Express.WriteDQM:
                outputModuleDetails.append( { 'dataTier' : tier0Config.Global.DQMDataTier,
                                              'eventContent' : tier0Config.Global.DQMDataTier,
                                              'primaryDataset' : specialDataset } )

            if runInfo['express_subscribe']:

                bindsPhEDExConfig.append( { 'RUN' : run,
                                            'PRIMDS' : specialDataset,
                                            'ARCHIVAL_NODE' : None,
                                            'TAPE_NODE' : None,
                                            'DISK_NODE' :  runInfo['express_subscribe']} )

                subscriptions.append( { 'nonCustodialSites' : [ runInfo['express_subscribe'] ],
                                        'nonCustodialSubType' : "Replica",
                                        'nonCustodialGroup' : "express",
                                        'autoApproveSites' : [ runInfo['express_subscribe'] ],
                                        'priority' : "high",
                                        'primaryDataset' : specialDataset,
                                        'deleteFromSource' : bool(runInfo['express_subscribe']) } )

            alcaSkim = None
            if len(streamConfig.Express.AlcaSkims) > 0:
                outputModuleDetails.append( { 'dataTier' : "ALCARECO",
                                              'eventContent' : "ALCARECO",
                                              'primaryDataset' : specialDataset } )
                alcaSkim = ",".join(streamConfig.Express.AlcaSkims)

                numPromptCalibProd = 0
                for producer in streamConfig.Express.AlcaSkims:
                    if producer.startswith("PromptCalibProd"):
                        numPromptCalibProd += 1

                if numPromptCalibProd > 0:
                    bindsPromptCalibration = { 'RUN' : run,
                                               'STREAM' : stream,
                                               'NUM_PRODUCER' : numPromptCalibProd }

            dqmSeq = None
            if len(streamConfig.Express.DqmSequences) > 0:
                dqmSeq = ",".join(streamConfig.Express.DqmSequences)

            streamConfig.Express.CMSSWVersion = streamConfig.VersionOverride.get(onlineVersion, onlineVersion)

            bindsCMSSWVersion.append( { 'VERSION' : streamConfig.Express.CMSSWVersion } )

            streamConfig.Express.ScramArch = tier0Config.Global.ScramArches.get(streamConfig.Express.CMSSWVersion,
                                                                                tier0Config.Global.DefaultScramArch)
            
            streamConfig.Express.RecoScramArch = None
            if streamConfig.Express.RecoCMSSWVersion != None:

                bindsCMSSWVersion.append( { 'VERSION' : streamConfig.Express.RecoCMSSWVersion } )

                streamConfig.Express.RecoScramArch = tier0Config.Global.ScramArches.get(streamConfig.Express.RecoCMSSWVersion,
                                                                                        tier0Config.Global.DefaultScramArch)

            bindsExpressConfig = { 'RUN' : run,
                                   'STREAM' : stream,
                                   'PROC_VER' : streamConfig.Express.ProcessingVersion,
                                   'WRITE_TIERS' : ",".join(streamConfig.Express.DataTiers),
                                   'WRITE_DQM' : streamConfig.Express.WriteDQM,
                                   'GLOBAL_TAG' : streamConfig.Express.GlobalTag,
                                   'MAX_RATE' : streamConfig.Express.MaxInputRate,
                                   'MAX_EVENTS' : streamConfig.Express.MaxInputEvents,
                                   'MAX_SIZE' : streamConfig.Express.MaxInputSize,
                                   'MAX_FILES' : streamConfig.Express.MaxInputFiles,
                                   'MAX_LATENCY' : streamConfig.Express.MaxLatency,
                                   'DQM_INTERVAL' : streamConfig.Express.PeriodicHarvestInterval,
                                   'BLOCK_DELAY' : streamConfig.Express.BlockCloseDelay,
                                   'CMSSW' : streamConfig.Express.CMSSWVersion,
                                   'SCRAM_ARCH' : streamConfig.Express.ScramArch,
                                   'RECO_CMSSW' : streamConfig.Express.RecoCMSSWVersion,
                                   'RECO_SCRAM_ARCH' : streamConfig.Express.RecoScramArch,
                                   'MULTICORE' : streamConfig.Express.Multicore,
                                   'ALCA_SKIM' : alcaSkim,
                                   'DQM_SEQ' : dqmSeq }

        #
        # then configure datasets
        #
        getStreamDatasetTriggersDAO = daoFactory(classname = "RunConfig.GetStreamDatasetTriggers")
        datasetTriggers = getStreamDatasetTriggersDAO.execute(run, stream, transaction = False)

        for dataset, paths in datasetTriggers.items():

            datasetConfig = retrieveDatasetConfig(tier0Config, dataset)

            selectEvents = []
            for path in sorted(paths):
                selectEvents.append("%s:%s" % (path, runInfo['process']))

            if streamConfig.ProcessingStyle == "Bulk":

                outputModuleDetails.append( { 'dataTier' : "RAW",
                                              'eventContent' : "ALL",
                                              'selectEvents' : selectEvents,
                                              'primaryDataset' : dataset } )

                if datasetConfig.ArchivalNode or datasetConfig.TapeNode or datasetConfig.DiskNode:

                    bindsPhEDExConfig.append( { 'RUN' : run,
                                                'PRIMDS' : dataset,
                                                'ARCHIVAL_NODE' : datasetConfig.ArchivalNode,
                                                'TAPE_NODE' : datasetConfig.TapeNode,
                                                'DISK_NODE' : datasetConfig.DiskNode } )

                custodialSites = []
                nonCustodialSites = []
                custodialAutoApproveSites = []
                nonCustodialAutoApproveSites = []
                if datasetConfig.ArchivalNode:
                    bindsStorageNode.append( { 'NODE' : datasetConfig.ArchivalNode } )
                    custodialSites.append(datasetConfig.ArchivalNode)
                    custodialAutoApproveSites.append(datasetConfig.ArchivalNode)
                if datasetConfig.TapeNode:
                    bindsStorageNode.append( { 'NODE' : datasetConfig.TapeNode } )
                    custodialSites.append(datasetConfig.TapeNode)
                if datasetConfig.DiskNode:
                    bindsStorageNode.append( { 'NODE' : datasetConfig.DiskNode } )
                    nonCustodialSites.append(datasetConfig.DiskNode)
                    nonCustodialAutoApproveSites.append(datasetConfig.DiskNode)

                if len(custodialSites) > 0:
                    subscriptions.append( { 'custodialSites' : custodialSites,
                                            'custodialSubType' : "Replica",
                                            'custodialGroup' : "DataOps",
                                            'autoApproveSites' : custodialAutoApproveSites,
                                            'priority' : "high",
                                            'primaryDataset' : dataset,
                                            'deleteFromSource' : True,
                                            'dataTier' : "RAW" } )
                if len(nonCustodialSites) > 0:
                    subscriptions.append( { 'nonCustodialSites' : nonCustodialSites,
                                            'nonCustodialSubType' : "Replica",
                                            'nonCustodialGroup' : "AnalysisOps",
                                            'autoApproveSites' : nonCustodialAutoApproveSites,
                                            'priority' : "high",
                                            'primaryDataset' : dataset,
                                            'deleteFromSource' : True,
                                            'dataTier' : "RAW" } )

                #
                # set subscriptions for error dataset
                #
                if datasetConfig.ArchivalNode != None:
                    subscriptions.append( { 'custodialSites' : [ datasetConfig.ArchivalNode ],
                                            'custodialSubType' : "Replica",
                                            'custodialGroup' : "DataOps",
                                            'autoApproveSites' : [ datasetConfig.ArchivalNode ],
                                            'priority' : "high",
                                            'primaryDataset' : "%s-Error" % dataset,
                                            'deleteFromSource' : True,
                                            'dataTier' : "RAW" } )


            elif streamConfig.ProcessingStyle == "Express":

                for dataTier in streamConfig.Express.DataTiers:
                    if dataTier not in [ "ALCARECO", "DQM", "DQMIO" ]:

                        outputModuleDetails.append( { 'dataTier' : dataTier,
                                                      'eventContent' : dataTier,
                                                      'selectEvents' : selectEvents,
                                                      'primaryDataset' : dataset } )

                if runInfo['express_subscribe']:

                    bindsPhEDExConfig.append( { 'RUN' : run,
                                                'PRIMDS' : dataset,
                                                'ARCHIVAL_NODE' : None,
                                                'TAPE_NODE' : None,
                                                'DISK_NODE' : runInfo['express_subscribe'] } )

                    subscriptions.append( { 'nonCustodialSites' : [ runInfo['express_subscribe'] ],
                                            'nonCustodialSubType' : "Replica",
                                            'nonCustodialGroup' : "express",
                                            'autoApproveSites' : [ runInfo['express_subscribe'] ],
                                            'priority' : "high",
                                            'primaryDataset' : dataset,
                                            'deleteFromSource' : bool(runInfo['express_subscribe']) } )

        #
        # finally create WMSpec
        #
        outputs = {}
        if streamConfig.ProcessingStyle == "Bulk":

            taskName = "Repack"
            workflowName = "Repack_Run%d_Stream%s" % (run, stream)

            specArguments = {}

            specArguments['Memory'] = 1000

            specArguments['RequestPriority'] = tier0Config.Global.BaseRequestPriority + 5000

            specArguments['CMSSWVersion'] = streamConfig.Repack.CMSSWVersion
            specArguments['ScramArch'] = streamConfig.Repack.ScramArch

            specArguments['ProcessingVersion'] = streamConfig.Repack.ProcessingVersion
            specArguments['MaxSizeSingleLumi'] = streamConfig.Repack.MaxSizeSingleLumi
            specArguments['MaxSizeMultiLumi'] = streamConfig.Repack.MaxSizeMultiLumi
            specArguments['MinInputSize'] = streamConfig.Repack.MinInputSize
            specArguments['MaxInputSize'] = streamConfig.Repack.MaxInputSize
            specArguments['MaxEdmSize'] = streamConfig.Repack.MaxEdmSize
            specArguments['MaxOverSize'] = streamConfig.Repack.MaxOverSize
            specArguments['MaxInputEvents'] = streamConfig.Repack.MaxInputEvents
            specArguments['MaxInputFiles'] = streamConfig.Repack.MaxInputFiles
            specArguments['MaxLatency'] = streamConfig.Repack.MaxLatency

            # parameters for repack direct to merge stageout
            specArguments['MinMergeSize'] = streamConfig.Repack.MinInputSize
            specArguments['MaxMergeEvents'] = streamConfig.Repack.MaxInputEvents

            specArguments['UnmergedLFNBase'] = "/store/unmerged/%s" % runInfo['bulk_data_type']
            if runInfo['backfill']:
                specArguments['MergedLFNBase'] = "/store/backfill/%s/%s" % (runInfo['backfill'],
                                                                            runInfo['bulk_data_type'])
            else:
                specArguments['MergedLFNBase'] = "/store/%s" % runInfo['bulk_data_type']

            specArguments['BlockCloseDelay'] = streamConfig.Repack.BlockCloseDelay

        elif streamConfig.ProcessingStyle == "Express":

            taskName = "Express"
            workflowName = "Express_Run%d_Stream%s" % (run, stream)

            specArguments = {}

            specArguments['TimePerEvent'] = streamConfig.Express.TimePerEvent
            specArguments['SizePerEvent'] = streamConfig.Express.SizePerEvent

            if streamConfig.Express.Scenario == "HeavyIonsRun2":
                baseMemory = 3000
                perCoreMemory = 1300
            else:
                baseMemory = 2000
                perCoreMemory = 900

            specArguments['Memory'] = baseMemory + perCoreMemory

            if streamConfig.Express.Multicore:
                specArguments['Multicore'] = streamConfig.Express.Multicore
                specArguments['Memory'] += (streamConfig.Express.Multicore - 1) * perCoreMemory

            specArguments['RequestPriority'] = tier0Config.Global.BaseRequestPriority + 10000

            specArguments['ProcessingString'] = "Express"
            specArguments['ProcessingVersion'] = streamConfig.Express.ProcessingVersion
            specArguments['Scenario'] = streamConfig.Express.Scenario

            specArguments['CMSSWVersion'] = streamConfig.Express.CMSSWVersion
            specArguments['ScramArch'] = streamConfig.Express.ScramArch
            specArguments['RecoCMSSWVersion'] = streamConfig.Express.RecoCMSSWVersion
            specArguments['RecoScramArch'] = streamConfig.Express.RecoScramArch

            specArguments['GlobalTag'] = streamConfig.Express.GlobalTag
            specArguments['GlobalTagTransaction'] = "Express_%d" % run
            specArguments['GlobalTagConnect'] = streamConfig.Express.GlobalTagConnect

            specArguments['MaxInputRate'] = streamConfig.Express.MaxInputRate
            specArguments['MaxInputEvents'] = streamConfig.Express.MaxInputEvents
            specArguments['MaxInputSize'] = streamConfig.Express.MaxInputSize
            specArguments['MaxInputFiles'] = streamConfig.Express.MaxInputFiles
            specArguments['MaxLatency'] = streamConfig.Express.MaxLatency
            specArguments['AlcaSkims'] = streamConfig.Express.AlcaSkims
            specArguments['DQMSequences'] = streamConfig.Express.DqmSequences
            specArguments['AlcaHarvestTimeout'] = runInfo['ah_timeout']
            specArguments['AlcaHarvestDir'] = runInfo['ah_dir']
            specArguments['DQMUploadProxy'] = dqmUploadProxy
            specArguments['DQMUploadUrl'] = runInfo['dqmuploadurl']
            specArguments['StreamName'] = stream
            specArguments['SpecialDataset'] = specialDataset

            specArguments['UnmergedLFNBase'] = "/store/unmerged/express"
            specArguments['MergedLFNBase'] = "/store/express"
            if runInfo['backfill']:
                specArguments['MergedLFNBase'] = "/store/backfill/%s/express" % runInfo['backfill']
            else:
                specArguments['MergedLFNBase'] = "/store/express"

            specArguments['PeriodicHarvestInterval'] = streamConfig.Express.PeriodicHarvestInterval

            specArguments['BlockCloseDelay'] = streamConfig.Express.BlockCloseDelay

        if streamConfig.ProcessingStyle in [ 'Bulk', 'Express' ]:

            specArguments['RunNumber'] = run
            specArguments['AcquisitionEra'] = runInfo['acq_era']
            specArguments['Outputs'] = outputModuleDetails
            specArguments['ValidStatus'] = "VALID"

            specArguments['SiteWhitelist'] = [ tier0Config.Global.ProcessingSite ]
            specArguments['SiteBlacklist'] = []

        if streamConfig.ProcessingStyle == "Bulk":
            factory = RepackWorkloadFactory()
            wmSpec = factory.factoryWorkloadConstruction(workflowName, specArguments)
            for subscription in subscriptions:
                wmSpec.setSubscriptionInformation(**subscription)
        elif streamConfig.ProcessingStyle == "Express":
            factory = ExpressWorkloadFactory()
            wmSpec = factory.factoryWorkloadConstruction(workflowName, specArguments)
            for subscription in subscriptions:
                wmSpec.setSubscriptionInformation(**subscription)

        if streamConfig.ProcessingStyle in [ 'Bulk', 'Express' ]:
            wmSpec.setOwnerDetails("Dirk.Hufnagel@cern.ch", "T0",
                                   { 'vogroup': 'DEFAULT', 'vorole': 'DEFAULT',
                                     'dn' : "Dirk.Hufnagel@cern.ch" } )

            wmSpec.setupPerformanceMonitoring(maxRSS = 1024 * specArguments['Memory'] + 10,
                                              maxVSize = 104857600, #100GB, effectively disabled
                                              softTimeout = 604800, #7 days, effectively disabled
                                              gracePeriod = 3600)

            wmbsHelper = WMBSHelper(wmSpec, taskName, cachepath = specDirectory)

        filesetName = "Run%d_Stream%s" % (run, stream)
        fileset = Fileset(filesetName)

        #
        # create workflow (currently either repack or express)
        #
        try:
            myThread.transaction.begin()
            if len(bindsCMSSWVersion) > 0:
                insertCMSSWVersionDAO.execute(bindsCMSSWVersion, conn = myThread.transaction.conn, transaction = True)
            if len(bindsDataset) > 0:
                insertDatasetDAO.execute(bindsDataset, conn = myThread.transaction.conn, transaction = True)
            if len(bindsStreamDataset) > 0:
                insertStreamDatasetDAO.execute(bindsStreamDataset, conn = myThread.transaction.conn, transaction = True)
            if len(bindsRepackConfig) > 0:
                insertRepackConfigDAO.execute(bindsRepackConfig, conn = myThread.transaction.conn, transaction = True)
            if len(bindsPromptCalibration) > 0:
                insertPromptCalibrationDAO.execute(bindsPromptCalibration, conn = myThread.transaction.conn, transaction = True)
            if len(bindsExpressConfig) > 0:
                insertExpressConfigDAO.execute(bindsExpressConfig, conn = myThread.transaction.conn, transaction = True)
            if len(bindsSpecialDataset) > 0:
                insertSpecialDatasetDAO.execute(bindsSpecialDataset, conn = myThread.transaction.conn, transaction = True)
            if len(bindsDatasetScenario) > 0:
                insertDatasetScenarioDAO.execute(bindsDatasetScenario, conn = myThread.transaction.conn, transaction = True)
            if len(bindsStorageNode) > 0:
                insertStorageNodeDAO.execute(bindsStorageNode, conn = myThread.transaction.conn, transaction = True)
            if len(bindsPhEDExConfig) > 0:
                insertPhEDExConfigDAO.execute(bindsPhEDExConfig, conn = myThread.transaction.conn, transaction = True)
            insertRunStreamDoneDAO.execute(bindsRunStreamDone, conn = myThread.transaction.conn, transaction = True)
            insertStreamStyleDAO.execute(bindsStreamStyle, conn = myThread.transaction.conn, transaction = True)
            if streamConfig.ProcessingStyle in [ 'Bulk', 'Express' ]:
                insertStreamFilesetDAO.execute(run, stream, filesetName, conn = myThread.transaction.conn, transaction = True)
                fileset.load()
                wmbsHelper.createSubscription(wmSpec.getTask(taskName), fileset, alternativeFilesetClose = True)
                insertWorkflowMonitoringDAO.execute([fileset.id],  conn = myThread.transaction.conn, transaction = True)
            if streamConfig.ProcessingStyle == "Bulk":
                bindsRecoReleaseConfig = []
                for fileset, primds in wmbsHelper.getMergeOutputMapping().items():
                    bindsRecoReleaseConfig.append( { 'RUN' : run,
                                                     'PRIMDS' : primds,
                                                     'FILESET' : fileset } )
                insertRecoReleaseConfigDAO.execute(bindsRecoReleaseConfig, conn = myThread.transaction.conn, transaction = True)
        except Exception as ex:
            logging.exception(ex)
            myThread.transaction.rollback()
            raise RuntimeError("Problem in configureRunStream() database transaction !")
        else:
            myThread.transaction.commit()

    else:

        # should we do anything for local runs ?
        pass
    return

def releasePromptReco(tier0Config, specDirectory, dqmUploadProxy):
    """
    _releasePromptReco_

    Called by Tier0Feeder

    Finds all run/primds that need to be released for PromptReco
    ( run.stop_time + reco_release_config.delay > now AND run.stop_time > 0 )

    Create workflows and subscriptions for the processing
    of runs/datasets.

    """
    logging.debug("releasePromptReco()")
    myThread = threading.currentThread()

    daoFactory = DAOFactory(package = "T0.WMBS",
                            logger = logging,
                            dbinterface = myThread.dbi)

    findRecoReleaseDatasetsDAO = daoFactory(classname = "RunConfig.FindRecoReleaseDatasets")
    findRecoReleaseDAO = daoFactory(classname = "RunConfig.FindRecoRelease")
    insertDatasetScenarioDAO = daoFactory(classname = "RunConfig.InsertDatasetScenario")
    insertCMSSWVersionDAO = daoFactory(classname = "RunConfig.InsertCMSSWVersion")
    insertRecoConfigDAO = daoFactory(classname = "RunConfig.InsertRecoConfig")
    insertStorageNodeDAO = daoFactory(classname = "RunConfig.InsertStorageNode")
    insertPhEDExConfigDAO = daoFactory(classname = "RunConfig.InsertPhEDExConfig")
    releasePromptRecoDAO = daoFactory(classname = "RunConfig.ReleasePromptReco")
    insertWorkflowMonitoringDAO = daoFactory(classname = "RunConfig.InsertWorkflowMonitoring")

    # mark workflows as injected
    wmbsDaoFactory = DAOFactory(package = "WMCore.WMBS",
                                logger = logging,
                                dbinterface = myThread.dbi)
    markWorkflowsInjectedDAO   = wmbsDaoFactory(classname = "Workflow.MarkInjectedWorkflows")

    #
    # handle PromptReco release for datasets
    #
    recoReleaseDatasets = findRecoReleaseDatasetsDAO.execute(transaction = False)

    datasetDelays = {}
    for dataset in recoReleaseDatasets:
        datasetConfig = retrieveDatasetConfig(tier0Config, dataset)
        datasetDelays[dataset] = (datasetConfig.RecoDelay, datasetConfig.RecoDelayOffset)

    recoRelease = findRecoReleaseDAO.execute(datasetDelays, transaction = False)
    for run in sorted(recoRelease.keys()):

        # for creating PromptReco specs
        recoSpecs = {}

        # for PhEDEx subscription settings
        subscriptions = []

        bindsDatasetScenario = []
        bindsCMSSWVersion = []
        bindsRecoConfig = []
        bindsStorageNode = []
        bindsReleasePromptReco = []

        # retrieve some basic run information
        getRunInfoDAO = daoFactory(classname = "RunConfig.GetRunInfo")
        runInfo = getRunInfoDAO.execute(run, transaction = False)[0]

        # retrieve phedex configs for run
        getPhEDExConfigDAO = daoFactory(classname = "RunConfig.GetPhEDExConfig")
        phedexConfigs = getPhEDExConfigDAO.execute(run, transaction = False)

        for (dataset, fileset, repackProcVer) in recoRelease[run]:

            bindsReleasePromptReco.append( { 'RUN' : run,
                                             'PRIMDS' : dataset,
                                             'NOW' : int(time.time()) } )

            datasetConfig = retrieveDatasetConfig(tier0Config, dataset)

            bindsDatasetScenario.append( { 'RUN' : run,
                                           'PRIMDS' : dataset,
                                           'SCENARIO' : datasetConfig.Scenario } )

            # check for era or run dependent config parameters
            datasetConfig.CMSSWVersion = extractConfigParameter(datasetConfig.CMSSWVersion, runInfo['acq_era'], run)
            datasetConfig.GlobalTag = extractConfigParameter(datasetConfig.GlobalTag, runInfo['acq_era'], run)
            datasetConfig.ProcessingVersion = extractConfigParameter(datasetConfig.ProcessingVersion, runInfo['acq_era'], run)

            bindsCMSSWVersion.append( { 'VERSION' : datasetConfig.CMSSWVersion } )

            alcaSkim = None
            if len(datasetConfig.AlcaSkims) > 0:
                alcaSkim = ",".join(datasetConfig.AlcaSkims)

            physicsSkim = None
            if len(datasetConfig.PhysicsSkims) > 0:
                physicsSkim = ",".join(datasetConfig.PhysicsSkims)

            dqmSeq = None
            if len(datasetConfig.DqmSequences) > 0:
                dqmSeq = ",".join(datasetConfig.DqmSequences)

            datasetConfig.ScramArch = tier0Config.Global.ScramArches.get(datasetConfig.CMSSWVersion,
                                                                         tier0Config.Global.DefaultScramArch)

            bindsRecoConfig.append( { 'RUN' : run,
                                      'PRIMDS' : dataset,
                                      'DO_RECO' : int(datasetConfig.DoReco),
                                      'RECO_SPLIT' : datasetConfig.RecoSplit,
                                      'WRITE_RECO' : int(datasetConfig.WriteRECO),
                                      'WRITE_DQM' : int(datasetConfig.WriteDQM),
                                      'WRITE_AOD' : int(datasetConfig.WriteAOD),
                                      'WRITE_MINIAOD' : int(datasetConfig.WriteMINIAOD),
                                      'PROC_VER' : datasetConfig.ProcessingVersion,
                                      'ALCA_SKIM' : alcaSkim,
                                      'PHYSICS_SKIM' : physicsSkim,
                                      'DQM_SEQ' : dqmSeq,
                                      'BLOCK_DELAY' : datasetConfig.BlockCloseDelay,
                                      'CMSSW' : datasetConfig.CMSSWVersion,
                                      'SCRAM_ARCH' : datasetConfig.ScramArch,
                                      'MULTICORE' : datasetConfig.Multicore,
                                      'GLOBAL_TAG' : datasetConfig.GlobalTag } )

            # check if the dataset has any phedex config
            if dataset in phedexConfigs:

                phedexConfig = phedexConfigs[dataset]

                tapeDataTiers = set()
                diskDataTiers = set()
                skimDataTiers = set()
                alcaDataTiers = set()

                if datasetConfig.WriteRECO:
                    diskDataTiers.add("RECO")
                if datasetConfig.WriteAOD:
                    tapeDataTiers.add("AOD")
                    diskDataTiers.add("AOD")
                if datasetConfig.WriteMINIAOD:
                    tapeDataTiers.add("MINIAOD")
                    diskDataTiers.add("MINIAOD")
                if datasetConfig.WriteDQM:
                    tapeDataTiers.add(tier0Config.Global.DQMDataTier)
                if len(datasetConfig.PhysicsSkims) > 0:
                    skimDataTiers.add("RAW-RECO")
                    skimDataTiers.add("USER")
                    skimDataTiers.add("RECO")
                    skimDataTiers.add("AOD")
                if len(datasetConfig.AlcaSkims) > 0:
                    alcaDataTiers.add("ALCARECO")

                # do things different based on whether we have TapeNode/DiskNode, only TapeNode or ArchivalNode
                if phedexConfig['tape_node'] != None:

                    if phedexConfig['disk_node'] == None:
                        diskDataTiers = set()

                    for dataTier in tapeDataTiers & diskDataTiers:
                        subscriptions.append( { 'custodialSites' : [phedexConfig['tape_node']],
                                                'custodialSubType' : "Replica",
                                                'custodialGroup' : "DataOps",
                                                'nonCustodialSites' : [phedexConfig['disk_node']],
                                                'nonCustodialSubType' : "Replica",
                                                'nonCustodialGroup' : "AnalysisOps",
                                                'autoApproveSites' : [phedexConfig['disk_node']],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'useSkim' : True,
                                                'isSkim' : False,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

                    for dataTier in skimDataTiers:
                        subscriptions.append( { 'custodialSites' : [phedexConfig['tape_node']],
                                                'custodialSubType' : "Replica",
                                                'custodialGroup' : "DataOps",
                                                'nonCustodialSites' : [phedexConfig['disk_node']] if phedexConfig['disk_node'] else [],
                                                'nonCustodialSubType' : "Replica",
                                                'nonCustodialGroup' : "AnalysisOps",
                                                'autoApproveSites' : [phedexConfig['disk_node']] if phedexConfig['disk_node'] else [],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'useSkim' : True,
                                                'isSkim' : True,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

                    for dataTier in tapeDataTiers - diskDataTiers:
                        subscriptions.append( { 'custodialSites' : [phedexConfig['tape_node']],
                                                'custodialSubType' : "Replica",
                                                'custodialGroup' : "DataOps",
                                                'autoApproveSites' : [],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'useSkim' : True,
                                                'isSkim' : False,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

                    for dataTier in alcaDataTiers:
                        subscriptions.append( { 'custodialSites' : [phedexConfig['tape_node']],
                                                'custodialSubType' : "Replica",
                                                'custodialGroup' : "DataOps",
                                                'autoApproveSites' : [],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'useSkim' : True,
                                                'isSkim' : True,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

                    for dataTier in diskDataTiers - tapeDataTiers:
                        subscriptions.append( { 'nonCustodialSites' : [phedexConfig['disk_node']],
                                                'nonCustodialSubType' : "Replica",
                                                'nonCustodialGroup' : "AnalysisOps",
                                                'autoApproveSites' : [phedexConfig['disk_node']],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'useSkim' : True,
                                                'isSkim' : False,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

                elif phedexConfig['archival_node'] != None:

                    for dataTier in tapeDataTiers | diskDataTiers | skimDataTiers | alcaDataTiers:

                        subscriptions.append( { 'custodialSites' : [phedexConfig['archival_node']],
                                                'custodialSubType' : "Replica",
                                                'custodialGroup' : "DataOps",
                                                'autoApproveSites' : [phedexConfig['archival_node']],
                                                'priority' : "high",
                                                'primaryDataset' : dataset,
                                                'deleteFromSource' : True,
                                                'dataTier' : dataTier } )

            writeTiers = []
            if datasetConfig.WriteRECO:
                writeTiers.append("RECO")
            if datasetConfig.WriteAOD:
                writeTiers.append("AOD")
            if datasetConfig.WriteMINIAOD:
                writeTiers.append("MINIAOD")
            if datasetConfig.WriteDQM:
                writeTiers.append(tier0Config.Global.DQMDataTier)
            if len(datasetConfig.AlcaSkims) > 0:
                writeTiers.append("ALCARECO")

            if datasetConfig.DoReco and len(writeTiers) > 0:

                #
                # create WMSpec
                #
                taskName = "Reco"
                workflowName = "PromptReco_Run%d_%s" % (run, dataset)

                specArguments = {}

                specArguments['TimePerEvent'] = datasetConfig.TimePerEvent
                specArguments['SizePerEvent'] = datasetConfig.SizePerEvent

                if datasetConfig.Scenario == "HeavyIonsRun2":
                    baseMemory = 3000
                    perCoreMemory = 1300
                else:
                    baseMemory = 2000
                    perCoreMemory = 900

                specArguments['Memory'] = baseMemory + perCoreMemory

                if datasetConfig.Multicore:
                    specArguments['Multicore'] = datasetConfig.Multicore
                    specArguments['Memory'] += (datasetConfig.Multicore - 1) * perCoreMemory

                specArguments['Memory'] += len(datasetConfig.PhysicsSkims) * 100

                specArguments['RequestPriority'] = tier0Config.Global.BaseRequestPriority

                specArguments['AcquisitionEra'] = runInfo['acq_era']
                specArguments['CMSSWVersion'] = datasetConfig.CMSSWVersion
                specArguments['ScramArch'] = datasetConfig.ScramArch

                specArguments['RunNumber'] = run

                specArguments['SplittingAlgo'] = "EventAwareLumiBased"
                specArguments['EventsPerJob'] = datasetConfig.RecoSplit

                specArguments['ProcessingString'] = "PromptReco"
                specArguments['ProcessingVersion'] = datasetConfig.ProcessingVersion
                specArguments['Scenario'] = datasetConfig.Scenario

                specArguments['GlobalTag'] = datasetConfig.GlobalTag
                specArguments['GlobalTagConnect'] = datasetConfig.GlobalTagConnect

                specArguments['InputDataset'] = "/%s/%s-%s/RAW" % (dataset, runInfo['acq_era'], repackProcVer)

                specArguments['WriteTiers'] = writeTiers
                specArguments['AlcaSkims'] = datasetConfig.AlcaSkims
                specArguments['PhysicsSkims'] = datasetConfig.PhysicsSkims
                specArguments['DQMSequences'] = datasetConfig.DqmSequences

                specArguments['UnmergedLFNBase'] = "/store/unmerged/%s" % runInfo['bulk_data_type']
                if runInfo['backfill']:
                    specArguments['MergedLFNBase'] = "/store/backfill/%s/%s" % (runInfo['backfill'],
                                                                                runInfo['bulk_data_type'])
                else:
                    specArguments['MergedLFNBase'] = "/store/%s" % runInfo['bulk_data_type']

                specArguments['ValidStatus'] = "VALID"

                specArguments['EnableHarvesting'] = "True"
                specArguments['DQMUploadProxy'] = dqmUploadProxy
                specArguments['DQMUploadUrl'] = runInfo['dqmuploadurl']

                specArguments['BlockCloseDelay'] = datasetConfig.BlockCloseDelay

                specArguments['SiteWhitelist'] = datasetConfig.SiteWhitelist
                specArguments['SiteBlacklist'] = []
                specArguments['TrustSitelists'] = "True"

                factory = PromptRecoWorkloadFactory()
                wmSpec = factory.factoryWorkloadConstruction(workflowName, specArguments)
                for subscription in subscriptions:
                    wmSpec.setSubscriptionInformation(**subscription)

                wmSpec.setOwnerDetails("Dirk.Hufnagel@cern.ch", "T0",
                                       { 'vogroup': 'DEFAULT', 'vorole': 'DEFAULT',
                                         'dn' : "Dirk.Hufnagel@cern.ch" } )

                wmSpec.setupPerformanceMonitoring(maxRSS = 1024 * specArguments['Memory'] + 10,
                                                  maxVSize = 104857600, #100GB, effectively disabled
                                                  softTimeout = 604800, #7 days, effectively disabled
                                                  gracePeriod = 3600)

                wmbsHelper = WMBSHelper(wmSpec, taskName, cachepath = specDirectory)

                recoSpecs[workflowName] = (wmbsHelper, wmSpec, fileset)

        try:
            myThread.transaction.begin()
            if len(bindsDatasetScenario) > 0:
                insertDatasetScenarioDAO.execute(bindsDatasetScenario, conn = myThread.transaction.conn, transaction = True)
            if len(bindsCMSSWVersion) > 0:
                insertCMSSWVersionDAO.execute(bindsCMSSWVersion, conn = myThread.transaction.conn, transaction = True)
            if len(bindsRecoConfig) > 0:
                insertRecoConfigDAO.execute(bindsRecoConfig, conn = myThread.transaction.conn, transaction = True)
            if len(bindsStorageNode) > 0:
                insertStorageNodeDAO.execute(bindsStorageNode, conn = myThread.transaction.conn, transaction = True)
            if len(bindsReleasePromptReco) > 0:
                releasePromptRecoDAO.execute(bindsReleasePromptReco, conn = myThread.transaction.conn, transaction = True)
            for (wmbsHelper, wmSpec, fileset) in recoSpecs.values():
                wmbsHelper.createSubscription(wmSpec.getTask(taskName), Fileset(id = fileset), alternativeFilesetClose = True)
                insertWorkflowMonitoringDAO.execute([fileset],  conn = myThread.transaction.conn, transaction = True)
            if len(recoSpecs) > 0:
                markWorkflowsInjectedDAO.execute(recoSpecs.keys(), injected = True, conn = myThread.transaction.conn, transaction = True)
        except Exception as ex:
            logging.exception(ex)
            myThread.transaction.rollback()
            raise RuntimeError("Problem in releasePromptReco() database transaction !")
        else:
            myThread.transaction.commit()

    return
