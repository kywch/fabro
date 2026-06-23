                migration_name, app_label))
        targets = [(app_label, migration.name)]

        # Show begin/end around output only for atomic migrations
        self.output_transaction = migration.atomic

        # Make a plan that represents just the requested migrations and show SQL
        # for it
