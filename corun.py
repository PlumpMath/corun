"""
corun is a coroutine-based Python library that uses only built-in Python 
features to provide a low-level event driven programming model to be used when 
you can't scale a very common thread based approach to 10K+ threads that need
to concurrently cooperate on the system. Its also the case that the tasks being
done by those threads is primarily I/O bound and not CPU bound as at that point
the coroutine approach may not perform as well as a regular threaded model 
would.
"""

import threading
import select
import time

from Queue import Queue

class Task(object):
    """
    the basic unit of work with a corun environment that represents the unit of
    work to be done at any given moment. this is also the object that holds
    the coroutine execution until the scheduler is ready to schedule this task
    back into execution
    """

    def __init__(self, target):
        # the hash is a great and free task id :)
        self.tid = self.__hash__()
        self.target = target
        self.sendval = None
        
    def run(self):
        """
        runs the task by sending the current sendval to the target generator 
        which is waiting a yield statement
        """
        return self.target.send(self.sendval)

class SystemCall(object):
    """
    system calls are special because they allow the system to do things such as
    wait for a given task to terminate or wait for I/O to be available on a 
    specific socket before giving control back to the task at hand.
    """
    def handle(self, scheduler, task):
        """
        system call handler which receives the current task that this system
        call is handling as well as the scheduler in case the system call 
        needs to interact with the scheduler.
        """
        pass

class KillTask(SystemCall):
    """
    System call to kill an existing coroutine by their taskid
    """
    def __init__(self, tid):
        SystemCall.__init__(self)
        self.tid = tid
        
    def handle(self, scheduler, task):
        """
        handle the killing of the specified task
        """
        dtask = scheduler.taskmap.get(self.tid)
        if dtask:
            dtask.target.close()
            task.sendval = True
        else:
            task.sendval = False
        scheduler.ready.put(task)    
        
class WaitForTask(SystemCall):
    """
    System call to wait for another Task to end
    """
    def __init__(self, tid):
        SystemCall.__init__(self)
        self.tid = tid
        
    def handle(self, scheduler, task):
        """
        handle the waiting for the specified task
        """
        result = scheduler.wait_for_exit(task, self.tid)
        task.sendval = result
        
        if not result:
            scheduler.ready.put(task)

class WaitForTime(SystemCall):
    """
    System call to wait for specified amoutn of time
    """
    def __init__(self, seconds):
        SystemCall.__init__(self)
        self.seconds = seconds
        
    def handle(self, scheduler, task):
        """
        handle the scheduling of when this task should be scheduled back into 
        the normal corun execution
        """
        # the idea here is that if the amount of time to sleep is in the 
        # millisecond range then we shouldn't be calculating the time to wake 
        # this task in more than milliseconds
        resolution = 1.0 / self.seconds
        exptime = time.time() + self.seconds
        exptime = int(exptime * resolution) / resolution
        scheduler.wait_for_time(task, exptime)

class ReadTask(SystemCall):
    """
    System call to wait for the specified file descriptor to have bytes to read
    """
    def __init__(self, fileobj):
        SystemCall.__init__(self)
        self.fileobj = fileobj
        
    def handle(self, scheduler, task):
        """
        places the current task into the io_waiting queue
        """
        fdesc = self.fileobj.fileno()
        scheduler.wait_for_read(task, fdesc)
        
class WriteTask(SystemCall):
    """
    System call to wait for the specified file descriptor to be able to write to
    """
    def __init__(self, fileobj):
        SystemCall.__init__(self)
        self.fileobj = fileobj
        
    def handle(self, scheduler, task):
        """
        places the current task into the io_waiting queue
        """
        fdesc = self.fileobj.fileno()
        scheduler.wait_for_write(task, fdesc) 

class Scheduler(threading.Thread):
    """
    The heart of the corun module that basically handles everything from 
    scheduling new tasks into the corun environment to handling that all dead
    tasks are correctly cleaned up after wards.
    """
    
    def __init__(self):
        threading.Thread.__init__(self)
        
        self.ready = Queue()
        self.taskmap = {}
        self.exit_waiting = {}
        
        self.io_waiting = {}
        self.time_waiting = {}
        self.epoll = select.epoll()
        
        self.running = True
        threading.Thread.start(self)
        
    def new(self, target):
        """
        takes the target function which should be a generator and puts it into 
        the corun scheduler to be executed as soon as possible.
        """
        newtask = Task(target)
        self.taskmap[newtask.tid] = newtask
        # schedule this task now!
        self.ready.put(newtask)
        return newtask.tid
    
    def wait_for_time(self, task, exptime):
        """
        blah blah
        """
        if not exptime in self.time_waiting.keys():
            self.time_waiting[exptime] = []
            
        self.time_waiting[exptime].append(task)
    
    def wait_for_read(self, task, fdesc):
        """
        blah blah
        """
        self.io_waiting[fdesc] = task
        self.epoll.register(fdesc, select.EPOLLIN)

    def wait_for_write(self, task, fdesc):
        """
        blah blah
        """
        self.io_waiting[fdesc] = task
        self.epoll.register(fdesc, select.EPOLLOUT)
            
    def wait_for_exit(self, task, waitid):
        """
        blah blah
        """
        if waitid in self.taskmap:
            self.taskmap.pop(task.tid)
            if waitid in self.exit_waiting.keys():
                self.exit_waiting[waitid].append(task)
            else:
                self.exit_waiting[waitid] = [task]   
            return True
        else:
            return False
        
    def __epoll(self, timeout):
        """
        blah blah
        """
        fdevents = self.epoll.poll(timeout)
            
        for (fdesc, _) in fdevents:
            self.ready.put(self.io_waiting.pop(fdesc))
            self.epoll.unregister(fdesc)
            
    def __io_epoll_task(self): 
        """
        epoll task that checks if currently awaiting io tasks can be dispatched
        """
        while True:
            if self.io_waiting != {}:
                if not(self.ready):
                    self.__epoll(-1)
                else:
                    self.__epoll(0)
            yield
   
    def __time_poll_task(self):
        """
        internal task that basically polls for tasks that are suppose to 
        execute at an instant of time in the future.
        """
        while True:
            current_time = time.time()
            for exptime in self.time_waiting.keys():
                if exptime <= current_time:
                    tasks = self.time_waiting.pop(exptime)
                    for task in tasks:
                        self.ready.put(task)
            yield
            
    def wait_for_tasks(self, coroutines, event): 
        """
        built-in scheduler task that waits for all of the coroutines identified
        before finishing
        """
        for tid in coroutines:
            if tid in self.taskmap:
                yield WaitForTask(tid)
        event.set()
           
    def joinall(self, coroutines):
        """
        wait for the specified coroutine ids to exit
        """
        event = threading.Event()
        self.new(self.wait_for_tasks(coroutines, event))
        event.wait()
    
    def shutdown(self):
        """
        tell the corun scheduler to shutdown 
        """
        self.running = False
        self.join()
        
    def run(self):
        self.new(self.__io_epoll_task())
        self.new(self.__time_poll_task())

        while self.running:
            task = self.ready.get()
            
            try:
                result = task.run()
                
                if isinstance(result, SystemCall):
                    result.handle(self, task)
                else:
                    self.ready.put(task)
            except StopIteration:
                del self.taskmap[task.tid]
            
                # notify others of exit
                if task.tid in self.exit_waiting.keys():
                    others = self.exit_waiting.pop(task.tid)
                    for other in others:
                        self.taskmap[other.tid] = other
                        self.ready.put(other)
def sleep(seconds):
    """
    wait for the specified amount of time before proceeding and allow the corun
    scheduler to handle other coroutines
    """
    yield WaitForTime(seconds)
 