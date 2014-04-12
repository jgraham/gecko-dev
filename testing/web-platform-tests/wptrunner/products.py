import os
import importlib

import moznetwork

import browser
import executor

here = os.path.join(os.path.split(__file__)[0])

def iter_products():
   product_dir = os.path.join(here, "browsers")
   plugin_files = [os.path.splitext(x)[0] for x in os.listdir(product_dir)
                   if not x.startswith("_") and x.endswith(".py")]

   for product in ["firefox", "servo", "b2g"]:
       yield product, None

   for fn in plugin_files:
       mod = importlib.import_module("wptrunner.browsers." + fn)
       if hasattr(mod, "__wptrunner__"):
           yield mod.__wptrunner__["product"], mod


def load_products():
    '''find all files in the plugin directory and imports them'''

    browser_classes = {"firefox": browser.FirefoxBrowser,
                       "servo": browser.ServoBrowser,
                       "b2g": browser.B2GBrowser}
    
    executor_classes = {"firefox": {"reftest": executor.MarionetteReftestExecutor,
                                    "testharness": executor.MarionetteTestharnessExecutor},
                        "servo": {"testharness": executor.ServoTestharnessExecutor},
                        "b2g": {"testharness": executor.B2GMarionetteTestharnessExecutor}}
    
    browser_kwargs = {"firefox": get_browser_kwargs,
                      "servo": get_browser_kwargs,
                      "b2g": get_browser_kwargs}
    
    env_options = {"firefox": get_env_options_desktop,
                   "servo": get_env_options_desktop,
                   "b2g": get_env_options_b2g}

    for name, mod in iter_products():
        if mod is None:
            assert name in browser_classes
            continue

        data = mod.__wptrunner__
        
        browser_classes[name] = getattr(mod, data["browser"])
        browser_kwargs[name] = getattr(mod, data["browser_kwargs"])
        env_options[name] = getattr(mod, data["env_options"])
        
        executor_classes[name] = {}
        for test_type, cls_name in data["executor"].iteritems():
            executor_classes[name][test_type] = getattr(mod, cls_name)
            
    return browser_classes, browser_kwargs, executor_classes, env_options


def get_browser_kwargs(product, binary, prefs_root):
    product = product

    browser_kwargs = {"binary": binary} if product in ("firefox", "servo") else {}
    if product in ("firefox", "b2g"):
        browser_kwargs["prefs_root"] = prefs_root

    return browser_kwargs


def get_env_options_desktop():
    return {"host": "localhost"}


def get_env_options_b2g():
    return {"host": moznetwork.get_ip()}


def get_executor_kwargs(http_server_url, timeout_multiplier):
    executor_kwargs = {"http_server_url": http_server_url,
                       "timeout_multiplier":timeout_multiplier}

    return executor_kwargs
