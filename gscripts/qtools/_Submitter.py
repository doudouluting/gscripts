#!/usr/bin/env python

__author__ = 'Patrick Liu, Olga Botvinnik, Michael Lovci '

# TODO: simplify Submitter()/write_sh() workflow. Right now it's confusing
# which options go where. (talk to Patrick)
# Also, add email option that checks for $EMAIL variable (Olga: also add this
#  to your miso pipeline script)

# To depend on a job array:
#    Array Dependencies
#        It  is  now possible to have a job depend on an array. These dependencies are in the form depend=arraydep:arrayid[num]. If [num] is not
#        present, then the dependencies applies to the entire array. If [num] is present, then num means the number of jobs that must  meet  the
#        condition for the dependency to be satisfied.
#    afterstartarray:arrayid[count]
#        This job may be scheduled for execution only after jobs in arrayid have started execution.
#
#    afterokarray:arrayid[count]
#        This job may be scheduled for execution only after jobs in arrayid have terminated with no errors.


from collections import defaultdict
import re
import math
import subprocess
from subprocess import PIPE
import sys

HOSTNAME = subprocess.Popen('hostname', stdout=subprocess.PIPE).communicate()[
    0].strip()

# Maximum number of jobs in an array job
MAX_ARRAY_JOBS = 500


class Submitter(object):
    """
    Class that will customize and submit shell scripts
    to the job scheduler

    How to use:

    """


    def __init__(self, commands, job_name, queue_type='PBS', sh_filename=None,
                 array=None, nodes=1, ppn=1,
                 walltime='0:30:00', queue='home', account='yeo-group',
                 out_filename=None, err_filename=None,
                 max_running=None, write_and_submit=False):
        """Constructor method, will initialize class attributes to passed
        keyword arguments and values.

        Parameters
        ----------
        commands : list of strings
            List of commands, each one will be on a separate line. If
            array=True, then each of these lines will be executed in a
            separate part of the array. Note: if there are more than 500
            elements in the list and array=True, then this will be broken up
            into several jobs, because there is a maximum of 500-element
            array jobs on TSCC.
        job_name : str
            Name of the job for the queue list
        queue_type : str
            Type of the submission queue, either "PBS" (tscc) or "SGE" (oolite)
        sh_filename : str
            File to write that will be submitted to the queue. By default,
            the job name + .sh
        array : bool
            Whether or not to write an array job. Default False.
        nodes : int
            Number of nodes to use on TSCC. Default 1.
        ppn : int
            Number of "processors per node" to use on TSCC. Default 1. Maximum
            is 16.
        walltime : str
            String of the format hours:minutes:seconds, e.g. '1:30:24' will
            submit a job for 1 hour, 30 minutes, and 24 seconds.
        queue : str
            Name of the queue, e.g. "home" for home-yeo or "glean" for glean
        account : str
            Account to associate with this job. Default 'yeo-group'. Usually
            fine for most jobs. This will be auto-adjusted for the glean and
            condo queues.
        out_filename : str
            Where to write stdout for the job. Defaults to sh_file.out
        err_filename : str
            Where to write stderr for the job. Defaults to sh_file.err
        max_running : int
            Maximum number of jobs running at once for an array job. 20 is
            reasonable.
        write_and_submit : bool
            Whether or not to also write and submit the script. Just
            instantiating this object does NOT submit any job. Need to do
            Submitter.job() afterwards. This is a convenience method for when
            submitting an array job with more than 500 commands.


        Returns
        -------
        job_id : int
            Job ID in the scheduler

        Raises
        ------
        ValueError : if more than 16 processors per node provided

        """
        self.additional_resources = defaultdict(list)

        self._array = array
        self._queue_type = queue_type
        # self.array = self._array if array is None else array
        # self.queue_type = self._queue_type if queue_type is None else queue_type

        if self.queue_type == 'SGE':
            self.add_resource("-l", 'bigmem')
            self.add_resource("-l", 'h_vmem=16G')

        if self.queue_type == 'PBS' and ppn > 16:
            raise ValueError('Cannot have more than 16 processors per node ('
                             'ppn). Tried to provide {}'.format(ppn))

        self.sh_filename = job_name + '.sh' if sh_filename is None \
            else sh_filename
        self.commands = commands
        self.job_name = job_name
        self.nodes = nodes
        self.ppn = ppn
        self.walltime = walltime
        self.queue = queue
        self.out_filename = self.sh_filename + '.out' if out_filename is None \
            else out_filename
        self.err_filename = self.sh_filename + '.err' if err_filename is None \
            else err_filename
        self.account = account
        self.max_running = max_running

        if write_and_submit:
            self.job(submit=True)


    @property
    def array(self):
        """Default value for whether or not to set this job as an array
        """
        if self._array is not None:
            # self._array is the user-supplied whether or not to use the array
            return self._array
        elif ("oolite" in HOSTNAME) or ("compute" in HOSTNAME):
            return True
        elif 'tscc' in HOSTNAME:
            return False

    @property
    def queue_type(self):
        """Default value for the queue type, auto-detects if we're on oolite
        or tscc
        """
        if self._queue_type is not None:
            # self._queue_type is the user-supplied queue type
            return self._queue_type
        elif ("oolite" in HOSTNAME) or ("compute" in HOSTNAME):
            return 'SGE'
        elif 'tscc' in HOSTNAME:
            return 'PBS'

    @property
    def number_jobs(self):
        """Get the number of jobs in the array
        """
        if self.array:
            return len(self.commands)
        else:
            return 1

    @property
    def queue_param_prefix(self):
        if self.queue_type == 'PBS':
            return '#PBS'
        elif self.queue_type == 'SGE':
            return '#$'

    @property
    def array_job_identifier(self):
        if self.queue_type == 'PBS':
            return "$PBS_ARRAYID"
        elif self.queue_type == 'SGE':
            return "$SGE_TASK_ID"

    def add_wait(self, wait_ID):
        """
        Add passed job ID to list of jobs for this job submission to
        wait for. Can be called multiple times.
        """
        if 'wait_for' not in self.data:
            self.data['wait_for'] = []

        self.data['wait_for'].append(str(wait_ID))

    def add_resource(self, kw, value):
        """
        Add passed keyword and value to a list of attributes that
        will be passed to the scheduler
        """
        self.additional_resources[kw].append(value)

    def write_sh(self, submit=False):
        """This will soon be deprecated. See Submitter.job() docstring
        """
        #for backwards compatibility
        self.job(submit=submit)

    def job(self, submit=False):
        """Writes the sh file and submits the job (if submit=True)

        Parameters
        ----------
        submit : bool
            Whether or not to submit the job

        Returns
        -------
        job_id : int
            Identifier of the job in the queue

        Raises
        ------

        """
        # PBS/TSCC does not allow array jobs with more than 500 commands
        if len(self.commands) > MAX_ARRAY_JOBS and self.array:
            commands = self.commands
            name = self.job_name
            commands_list = [commands[i:(i + MAX_ARRAY_JOBS)]
                             for i in xrange(0, len(commands), MAX_ARRAY_JOBS)]
            for i, commands in enumerate(commands_list):
                job_name = '{}{}'.format(name, i + 1)
                sh_filename = '{}{}.sh'.format(self.sh_filename.rstrip('.sh'),
                                               i + 1)
                sub = Submitter(commands=commands, job_name=job_name,
                                sh_filename=sh_filename, array=True,
                                walltime=self.walltime, ppn=self.ppn,
                                nodes=self.nodes, queue=self.queue,
                                queue_type=self.queue_type,
                                write_and_submit=True)
                # sub.write_sh(**kwargs)
            return

        # sys.stderr.write(self.sh_filename)
        sh_file = open(self.sh_filename, 'w')
        sh_file.write("#!/bin/bash\n")

        sh_file.write("%s -N %s\n" % (self.queue_param_prefix, self.job_name))
        sh_file.write("%s -o %s\n" % (self.queue_param_prefix,
                                      self.out_filename))
        sh_file.write("%s -e %s\n" % (self.queue_param_prefix,
                                      self.err_filename))
        sh_file.write("%s -V\n" % self.queue_param_prefix)

        if self.queue_type == 'SGE':
            self._write_sge(sh_file)

        elif self.queue_type == 'PBS':
            self._write_pbs(sh_file)

        if self.array:
            sys.stderr.write("running %d tasks as an array-job.\n" % (len(
                self.commands)))
            for i, cmd in enumerate(self.commands):
                sh_file.write("cmd[%d]=\"%s\"\n" % ((i + 1), cmd))
            sh_file.write("eval ${cmd[%s]}\n" % (self.array_job_identifier))
        #    pass
        else:
            for command in self.commands:
                sh_file.write(str(command) + "\n")
        sh_file.write('\n')

        sh_file.close()
        if submit:
            p = subprocess.Popen(["qsub", self.sh_filename],
                                 stdout=PIPE)
            output = p.communicate()[0].strip()
            job_id = re.findall(r'\d+', output)[0]
            sys.stderr.write("job ID: %s\n" % job_id)

            return job_id
        else:
            return 0

    def _write_pbs(self, sh_file):
        """PBS-queue (TSCC) specific header formatting
        """
        # queue_param_prefix = '#PBS'
        #            queue_param_prefix = '#PBS'
        sh_file.write("%s -l walltime=%s\n" % (self.queue_param_prefix,
                                               self.walltime))
        sh_file.write("%s -l nodes=%s:ppn=%s\n" % (self.queue_param_prefix,
                                                   str(self.nodes),
                                                   str(self.ppn)))
        sh_file.write("%s -A %s\n" % (self.queue_param_prefix, self.account))
        sh_file.write("%s -q %s\n" % (self.queue_param_prefix, self.queue))

        # Workaround to submit to 'glean' queue and 'condo' queue
        #commented out for now, jim updated this, and it breaks automatic condo submissions
        #if (self.queue == "glean") or (self.queue == "condo"):
        #    sh_file.write('%s -W group_list=condo-group\n' %
        #                  self.queue_param_prefix)

        self._write_additional_resources(sh_file)

        if self.array:
            if self.max_running is not None:
                sh_file.write("%s -t 1-%d%%%d\n" % (
                    self.queue_param_prefix, self.number_jobs,
                    self.max_running))
            else:
                sh_file.write(
                    "%s -t 1-%d\n" % (self.queue_param_prefix,
                                      self.number_jobs))

        sh_file.write('\n# Go to the directory from which the script was '
                      'called\n')
        sh_file.write("cd $PBS_O_WORKDIR\n")
        # self.array_job_identifier = "$PBS_ARRAYID"

    def _write_sge(self, sh_file):
        """SGE-queue (oolit) specific header formatting
        """
        # queue_param_prefix = '#$'
        sh_file.write("%s -S /bin/bash\n" % self.queue_param_prefix)
        sh_file.write("%s -cwd\n" % self.queue_param_prefix)
        self._write_additional_resources(sh_file)


    def _write_additional_resources(self, sh_file):
        if self.additional_resources:
            # if self.data['additional_resources']:
            for key, value in self.additional_resources.iteritems():
                # for value in self.data['additional_resources'][key]:
                for v in value:
                    sh_file.write("%s %s %s\n" % (self.queue_param_prefix,
                                                  key, v))
