import unittest

from remote_dev.proxy import ProxySettings, ReverseProxyTunnel, choose_remote_port, ssh_reverse_forward_args


class ProxyTests(unittest.TestCase):
    def test_proxy_settings_and_random_port(self) -> None:
        settings = ProxySettings(remote_port_min=41000, remote_port_max=41010)
        settings.validate()
        self.assertTrue(41000 <= choose_remote_port(settings) <= 41010)


    def test_reverse_forward_is_loopback_and_disables_multiplexing(self) -> None:
        settings = ProxySettings(host="127.0.0.1", port=4227)
        args = ssh_reverse_forward_args(settings, 45678)
        self.assertIn("ControlMaster=no", args)
        self.assertIn("ControlPath=none", args)
        self.assertIn("127.0.0.1:45678:127.0.0.1:4227", args)


    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProxySettings(mode="bad").validate()

    def test_keyless_key_mode_keeps_global_ssh_config(self) -> None:
        tunnel = ReverseProxyTunnel(settings=ProxySettings(), destination="zylona@example", identity_file=None)
        command = tunnel.command()
        self.assertNotIn("-F", command)
        self.assertNotIn("-i", command)

    def test_explicit_key_mode_isolated_from_global_ssh_config(self) -> None:
        tunnel = ReverseProxyTunnel(settings=ProxySettings(), destination="zylona@example", identity_file="/tmp/key")
        command = tunnel.command()
        self.assertIn("-F", command)
        self.assertIn("/dev/null", command)
        self.assertIn("-i", command)
