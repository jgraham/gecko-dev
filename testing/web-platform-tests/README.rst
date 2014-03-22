web-platform-tests Harness
==========================

This harness is designed for running the W3C web-platform-tests
testsuite, which is stored on `github`_.

Running the Tests
-----------------

If you are using a Gecko checkout, tests are typically run using `mach`::

  mach web-platform-tests

It is possible to control exactly which tests will be run, where the
output will go and other settings of the test runner. See

::

  mach help web-platform-tests

for more details.

In other scenarios tests are run using the `runtests.py` file. Again
this has documentation obtained from

::

  python runtests.py --help

Implementation Details
----------------------

The implementation code lives under `wpttests`. In addition, two
further directories are required, one containing the tests to be
run and the other containing the metadata describing the tests and the
expected result of each test.

When running in a gecko tree, those directories are::

`tests/`
  These are the imported test files. Everthing in this
  directory will be wiped on update and so it should should be
  considered read-only (for now at least, a streamlined procedure for
  updating tests without submitting to the web-platform-tests
  repository directly is a future goal).

`metadata/`
  This contains the metadata required to run the tests and to
  determine whether the results match expectations or not. The
  `MANIFEST.json` file in this directory is generated when the tests
  are imported and should not be hand-edited. Expected results are
  stored in ini files, described below, and must be updated when
  patches affect the expected test results.

In other cases the path to those directories muct be provided on the
command line.

Expectation File Format
~~~~~~~~~~~~~~~~~~~~~~~

Metadat about tests, notably including their expected results, is
stored in a modified ini-like format that is designed to be human
editable, but also to be machine updatable.

Each test file that requires metadata to be specified (because it has
a non-default expectation or because it is disabled, for example) has
a corresponding expectation file in the `metadata` directory. For
example a test file `html/test1.html` containing a failing test would
have an expectation file called `html/test1.html.ini` in the
`metadata` directory.

An example of an expectation file is::

  example_default_key: example_value

  [filename.html]
    type: testharness

    [subtest1]
      expected: FAIL

    [subtest2]
      expected:
        if platform == 'win': TIMEOUT
        if platform == 'osx': ERROR
        FAIL

  [filename.html?query=something]
    type: testharness
    disabled: bug12345

The file consists of two elements, key-value pairs and
sections.

Sections are delimited by headings enclosed in square brackets. Any
closing square bracket in the heading itself my be escaped with a
backslash. Each section may then contain any number of key-value pairs
followed by any number of subsections. So that it is clear which data
belongs to each section without the use of end-section markers, the
data for each section (i.e. the key-value pairs and subsections) must
be indented using spaces. Indentation need only be consistent, but
using two spaces per level is recommended.

In a test expectation file, each resource provided by the file has a
single section, with the section heading being the part after the last
`/` in the test url. Tests that have subsections may have subsections
for those subtests in which the heading is the name of the subtest.

Simple key-value pairs are of the form::

  key: value

Note that unlike ini files, only `:` is a valid seperator; `=` will
not work as expected. Key-value pairs may also have conditional
values of the form::

  key:
    if condition1: value1
    if condition2: value2
    default

In this case each conditional is evaluated in turn and the value is
that on the right hand side of the first matching conditional. In the
case that no condition matches, the unconditional default is used. If
no condition matches and no default is provided it is equivalent to
the key not being present. Conditionals use a simple python-like expression
language e.g.::

  if debug and (platform == "linux" or platform == "osx"): FAIL

For test expectations the avaliable variables are those in the
`run_info` which for desktop are `version`, `os`, `bits`, `processor`,
`debug` and `product`.

Key-value pairs specified at the top level of the file before any
sections are special as they provide defaults for the rest of the file
e.g.::

  key1: value1

  [section 1]
    key2: value2

  [section 2]
    key1: value3

In this case, inside section 1, `key1` would have the value `value1`
and `key2` the value `value2` whereas in section 2 `key1` would have
the value `value3` and `key2` would be undefined.

The web-platform-test harness knows about several keys:

`expected`
  Must evaluate to a possible test status indicating the expected
  result of the test. The implicit default is PASS or OK when the
  field isn't present.

`disabled`
  Any value indicates that the test is disabled.

`type`
  The test type e.g. `testharness` or `reftest`.

`reftype`
  The type of comparison for reftests; either `==` or `!=`.

`refurl`
  The reference url for reftests.

Updating the Tests
------------------

Running in a gecko tree, the `tests` directory and the `MANIFEST.json`
file in the `metadata` directory can be synced using `mach
web-platform-tests-update`. The same command can be used in
conjunction with the raw logs from a set of test runs to update the
expected data in the `metadata` directory.

Outside of a gecko tree, the `update.py` script may be used to perform
the update; a `config.ini` must be provided to point to the correct
location for the update files.

_github: https://github.com/w3c/web-platform-tests
