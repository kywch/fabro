            self.assertNotIn(connection.ops.start_transaction_sql().lower(), queries)
        self.assertNotIn(connection.ops.end_transaction_sql().lower(), queries)

    @override_settings(
        INSTALLED_APPS=[
            "migrations.migrations_test_apps.migrated_app",
