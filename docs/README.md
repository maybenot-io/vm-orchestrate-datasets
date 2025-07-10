# General Documentation

This directory will hold documentation about different collections, VM structures, how they are performed, and all things related.

## Quality of Experience & other metadata

In all of these collections, specific metadata about the website visits is gathered using available Performance API:s.
This metadata is tracked to assess how the VPN connection affects the 'feel' of the website visit in terms of load times.

Fetching the metadata is done by passing JavaScript through selenium to run in the browser.
To avoid the issue of cached responses for each new visit, several anti-caching flags are passed to the selenium profile and options for the browser.
Some of these flags may be (likely are) redundant, but better safe than sorry, to make sure nothing is accidentally cached and messing with the metrics:
```python
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile

profile = FirefoxProfile()
profile.set_preference("browser.cache.disk.enable", False)
profile.set_preference("privacy.clearOnShutdown.cache", True)
profile.set_preference("privacy.clearOnShutdown.cookies", True)
profile.set_preference("privacy.clearOnShutdown.history", True)
```
A PerformanceObserver instance is used to watch for these QoE metrics in the Mullvad Browser that selenium runs, as shown below:
```js
const observer = new PerformanceObserver((list) => {
    list.getEntries().forEach(entry => {
        const type = entry.entryType;
        window.performanceMetrics[type] = window.performanceMetrics[type] || [];
        window.performanceMetrics[type].push(entry.toJSON());
    });
});
observer.observe({ type: 'navigation', buffered: true });
observer.observe({ type: 'resource', buffered: true });
observer.observe({ type: 'paint', buffered: true });
observer.observe({ type: 'largest-contentful-paint', buffered: true });
```

## Editing the ISO files

Each VM currently undergoes initial necessary configuration by injecting/planting specific scripts inside the ISO, usually one script that is marked as autostart or set to execute on first boot.
This script handles most if not all of the rest of the configuration for the data collection (installing required packages, cloning repository for the collection scripts, etc.)

The way these are currently being edited is by using [Cubic](https://github.com/PJ-Singh-001/Cubic) to unpack, chroot and repack the ISO after making all necessary changes.
Cubic isn’t strictly necessary for this editing, but it simplifies the process.
