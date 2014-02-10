#Special value used as a sentinal in various commands
Stop = object()


class TestRunner(object):
    def __init__(self, http_server_url, command_pipe, marionette_port=None, binary=None):
        """Base class for actually running tests.

        Each test type will have a derived class overriding the do_test
        and convert_result methods.

        Each instance of this class is expected to run in its own process
        and each operates its own event loop with two basic commands, one
        to run a test and one to shut down the instance.

        :param http_server_url: url to the main http server over which tests
                                will be loaded
        :param command_pipe: subprocess.Pipe used to send commands to the
                             process
        :param marionette_port: port number to use for marionette, or None
                                to use a free port
        """
        self.http_server_url = http_server_url
        self.command_pipe = command_pipe
        if marionette_port is None:
            marionette_port = get_free_port(2828)
        self.marionette_port = marionette_port
        self.binary = binary
        self.timer = None
        self.window_id = str(uuid.uuid4())
        self.timeout_multiplier = 1  # TODO: Adjust this depending on tests and hardware

    def setup(self):
        """Connect to browser via marionette"""
        logger.debug("Connecting to marionette on port %i" % self.marionette_port)
        self.browser = marionette.Marionette(host='localhost', port=self.marionette_port)
        #XXX Move this timeout somewhere
        success = self.browser.wait_for_port(20)
        if success:
            logger.debug("Marionette port aquired")
            self.browser.start_session()
            logger.debug("Marionette session started")
            self.send_message("init_succeeded")
        else:
            logger.error("Failed to connect to marionette")
            self.send_message("init_failed")

        if success:
            self.browser.navigate(urlparse.urljoin(self.http_server_url, "/gecko_runner.html"))
            self.browser.execute_script("document.title = '%s'" % threading.current_thread().name)

        return success

    def teardown(self):
        self.command_pipe.close()
        #Close the marionette session

    def run(self):
        """Main loop accepting commands over the pipe and triggering
        the associated methods"""
        logger.debug("Run TestRunner")
        self.setup()
        commands = {"run_test": self.run_test,
                    "stop": self.stop}
        try:
            while True:
                command, args = self.command_pipe.recv()
                try:
                    rv = commands[command](*args)
                except Exception:
                    logger.error("Error running command %s with arguments %r:\n%s" %
                                 (command, args, traceback.format_exc()))
                else:
                    if rv is Stop:
                        break
        finally:
            self.teardown()

    def connection_alive(self):
        try:
            #Get a simple property over the connection
            self.browser.current_window_handle
        except (socket.timeout, marionette.errors.InvalidResponseException):
            return False
        return True

    def run_test(self, test):
        """Run a single test.

        This method is independent of the test type, and calls
        do_test to implement the type-sepcific testing functionality.
        """
        if not self.connection_alive():
            logger.error("Lost marionette connection")
            self.send_message("restart_test", test)
            return Stop

        #Lock to prevent races between timeouts and other results
        #This might not be strictly necessary if we need to deal
        #with the result changing post-hoc anyway (e.g. due to detecting
        #a crash after we get the data back from marionette)
        result = None
        result_flag = threading.Event()
        result_lock = threading.Lock()

        def timeout_func():
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("EXTERNAL-TIMEOUT", None), [])
                    self.send_message("test_ended", test, result)

        self.timer = threading.Timer(test.timeout + 10, timeout_func)
        self.timer.start()

        self.browser.set_script_timeout((test.timeout + 5) * 1000)

        try:
            result = self.convert_result(test, self.do_test(test))
        except marionette.errors.ScriptTimeoutException:
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("EXTERNAL-TIMEOUT", None), [])
            #Clean up any unclosed windows
            #This doesn't account for the possibility the browser window
            #is totally hung. That seems less likely since we are still
            #getting data from marionette, but it might be just as well
            #to do a full restart in this case
            #XXX - this doesn't work at the moment because window_handles
            #only returns OS-level windows (see bug 907197)
            # while True:
            #     handles = self.browser.window_handles
            #     self.browser.switch_to_window(handles[-1])
            #     if len(handles) > 1:
            #         self.browser.close()
            #     else:
            #         break
            #Now need to check if the browser is still responsive and restart it if not
        except (socket.timeout, marionette.errors.InvalidResponseException) as e:
            #This can happen on a crash
            #XXX Maybe better to have a specific crash message?
            #Also, should check after the test if the firefox process is still running
            #and otherwise ignore any other result and set it to crash
            with result_lock:
                if not result_flag.is_set():
                    result_flag.set()
                    result = (test.result_cls("CRASH", None), [])
            logger.warning(unicode(e))
        finally:
            self.timer.cancel()

        with result_lock:
            if result:
                self.send_message("test_ended", test, result)

    def do_test(self, test):
        raise NotImplementedError

    def convert_result(self, test, result):
        raise NotImplementedError

    def stop(self):
        return Stop

    def send_message(self, command, *args):
        self.command_pipe.send((command, args))        if node.data:
            rv.append("")
