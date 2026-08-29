-- 0001_initial_schema.sql
-- T-046: SQL schema for every table in plan.md §3, targeting Azure SQL (T-SQL).
--
-- Conventions:
--   * Explicitly named keys (vendor_id, document_id, invoice_id, po_line_id, model_id) are
--     application-supplied string IDs (Invoice/Vendor/etc. in src/core/models.py all carry
--     `str` IDs — this schema does not impose a generation strategy).
--   * Generic "id PK" tables from plan.md §3 (invoice_fields, invoice_lines,
--     validation_results, corrections, audit_log, goods_receipts) use BIGINT IDENTITY.
--   * Money fields are DECIMAL(19,2) — matches the 2-decimal-place guarantee from
--     core.validation.normalise.normalise_amount(); nothing upstream produces more
--     precision than that once normalisation has run.
--   * Quantities are DECIMAL(19,4) to allow fractional units (weight, length, etc.).
--   * There is no native JSON type in Azure SQL; JSON columns are NVARCHAR(MAX) with an
--     ISJSON check.
--   * Enum-shaped columns are NVARCHAR with a CHECK constraint whose values match the
--     corresponding Python StrEnum in src/core/models.py exactly — keep these in sync.

-- ============================================================================
-- vendors
-- ============================================================================
CREATE TABLE dbo.vendors (
    vendor_id           NVARCHAR(64)    NOT NULL PRIMARY KEY,
    name                NVARCHAR(256)   NOT NULL,
    normalised_name     NVARCHAR(256)   NOT NULL,
    tax_id              NVARCHAR(32)    NULL,
    address             NVARCHAR(512)   NULL,
    bank_account        NVARCHAR(64)    NULL,
    currency            CHAR(3)         NULL,
    requires_po         BIT             NOT NULL DEFAULT 1,
    auto_threshold      FLOAT           NULL,
    review_threshold    FLOAT           NULL,
    max_auto_amount     DECIMAL(19, 2)  NULL,
    invoices_processed  INT             NOT NULL DEFAULT 0,
    is_active           BIT             NOT NULL DEFAULT 1,
    created_at          DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_vendors_tax_id UNIQUE (tax_id),
    CONSTRAINT CK_vendors_auto_threshold CHECK (auto_threshold IS NULL OR auto_threshold BETWEEN 0.0 AND 1.0),
    CONSTRAINT CK_vendors_review_threshold CHECK (review_threshold IS NULL OR review_threshold BETWEEN 0.0 AND 1.0)
);
GO

-- Supports BR-12's fuzzy candidate pool (VendorLookup.active_vendors) being pre-filtered
-- rather than a full table scan as the vendor master grows.
CREATE INDEX IX_vendors_normalised_name ON dbo.vendors (normalised_name) WHERE is_active = 1;
GO

-- ============================================================================
-- documents
-- ============================================================================
CREATE TABLE dbo.documents (
    document_id         NVARCHAR(64)    NOT NULL PRIMARY KEY,
    content_hash         NVARCHAR(64)    NOT NULL,
    -- Not constrained to a fixed CHECK list: plan.md §3a names five ingestion channels
    -- (whatsapp, email, api, portal, blob/watched-folder) but doesn't mandate this being
    -- closed, and new channels shouldn't require a migration to add.
    source               NVARCHAR(32)    NOT NULL,
    original_filename    NVARCHAR(512)   NULL,
    sender               NVARCHAR(256)   NULL,
    blob_uri             NVARCHAR(1024)  NOT NULL,
    page_count           INT             NOT NULL,
    received_at          DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    parent_document_id   NVARCHAR(64)    NULL,
    page_range_start     INT             NULL,
    page_range_end       INT             NULL,
    status                NVARCHAR(32)    NOT NULL DEFAULT 'received',
    CONSTRAINT FK_documents_parent FOREIGN KEY (parent_document_id) REFERENCES dbo.documents (document_id),
    CONSTRAINT CK_documents_page_range CHECK (
        (page_range_start IS NULL AND page_range_end IS NULL)
        OR (page_range_start IS NOT NULL AND page_range_end IS NOT NULL AND page_range_end >= page_range_start)
    )
);
GO

-- FR-003: the cache lookup this index backs is the whole point of Article XII.
CREATE INDEX IX_documents_content_hash ON dbo.documents (content_hash);
GO
CREATE INDEX IX_documents_parent ON dbo.documents (parent_document_id) WHERE parent_document_id IS NOT NULL;
GO

-- ============================================================================
-- invoices
-- ============================================================================
CREATE TABLE dbo.invoices (
    invoice_id          NVARCHAR(64)    NOT NULL PRIMARY KEY,
    document_id         NVARCHAR(64)    NOT NULL,
    vendor_id           NVARCHAR(64)    NULL,
    invoice_number      NVARCHAR(128)   NULL,
    invoice_date        DATE            NULL,
    due_date            DATE            NULL,
    po_number           NVARCHAR(64)    NULL,
    currency            CHAR(3)         NULL,
    subtotal            DECIMAL(19, 2)  NULL,
    tax_total           DECIMAL(19, 2)  NULL,
    total                DECIMAL(19, 2)  NULL,
    amount_due           DECIMAL(19, 2)  NULL,
    state                NVARCHAR(32)    NOT NULL DEFAULT 'received',
    routing_decision     NVARCHAR(32)    NULL,
    routing_reason       NVARCHAR(128)   NULL,
    overall_confidence   FLOAT           NULL,
    model_id             NVARCHAR(64)    NULL,
    model_version        NVARCHAR(32)    NULL,
    extracted_at         DATETIME2(3)    NULL,
    posted_at            DATETIME2(3)    NULL,
    CONSTRAINT FK_invoices_document FOREIGN KEY (document_id) REFERENCES dbo.documents (document_id),
    CONSTRAINT FK_invoices_vendor FOREIGN KEY (vendor_id) REFERENCES dbo.vendors (vendor_id),
    CONSTRAINT CK_invoices_state CHECK (state IN (
        'received', 'classified', 'extracted', 'validated', 'matched', 'routed',
        'straight_through', 'in_review', 'exception', 'approved', 'rejected',
        'handoff', 'handoff_failed', 'released', 'quarantined'
    )),
    CONSTRAINT CK_invoices_routing_decision CHECK (routing_decision IS NULL OR routing_decision IN (
        'straight_through', 'review', 'full_review', 'exception', 'rejected'
    ))
);
GO

-- BR-04 enforced at the database level, not only in application code (plan.md §3). SQL
-- Server has no native "unique except NULL" constraint, so this is a filtered unique index.
CREATE UNIQUE INDEX UQ_invoices_vendor_invoice_number
    ON dbo.invoices (vendor_id, invoice_number)
    WHERE vendor_id IS NOT NULL AND invoice_number IS NOT NULL;
GO
CREATE INDEX IX_invoices_document ON dbo.invoices (document_id);
GO
CREATE INDEX IX_invoices_state ON dbo.invoices (state);
GO

-- ============================================================================
-- invoice_fields
-- ============================================================================
CREATE TABLE dbo.invoice_fields (
    id            BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    invoice_id    NVARCHAR(64)    NOT NULL,
    field_name    NVARCHAR(64)    NOT NULL,
    value         NVARCHAR(MAX)   NULL,
    confidence    FLOAT           NOT NULL,
    page          INT             NULL,
    polygon       NVARCHAR(MAX)   NULL,
    source         NVARCHAR(16)    NOT NULL DEFAULT 'model',
    needs_review   BIT             NOT NULL DEFAULT 0,
    CONSTRAINT FK_invoice_fields_invoice FOREIGN KEY (invoice_id) REFERENCES dbo.invoices (invoice_id),
    CONSTRAINT CK_invoice_fields_confidence CHECK (confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT CK_invoice_fields_source CHECK (source IN ('model', 'human', 'rule', 'default')),
    CONSTRAINT CK_invoice_fields_polygon_json CHECK (polygon IS NULL OR ISJSON(polygon) = 1),
    CONSTRAINT UQ_invoice_fields_invoice_field UNIQUE (invoice_id, field_name)
);
GO
CREATE INDEX IX_invoice_fields_invoice ON dbo.invoice_fields (invoice_id);
GO

-- ============================================================================
-- invoice_lines
-- ============================================================================
CREATE TABLE dbo.invoice_lines (
    id                  BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    invoice_id          NVARCHAR(64)    NOT NULL,
    line_number         INT             NOT NULL,
    description         NVARCHAR(512)   NULL,
    quantity            DECIMAL(19, 4)  NOT NULL DEFAULT 0,
    unit_price          DECIMAL(19, 2)  NOT NULL DEFAULT 0,
    amount              DECIMAL(19, 2)  NOT NULL DEFAULT 0,
    tax_rate            DECIMAL(6, 3)   NULL,
    product_code        NVARCHAR(64)    NULL,
    row_confidence       FLOAT           NOT NULL,
    matched_po_line_id   NVARCHAR(64)    NULL,
    CONSTRAINT FK_invoice_lines_invoice FOREIGN KEY (invoice_id) REFERENCES dbo.invoices (invoice_id),
    CONSTRAINT CK_invoice_lines_row_confidence CHECK (row_confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT UQ_invoice_lines_invoice_line UNIQUE (invoice_id, line_number)
);
GO
CREATE INDEX IX_invoice_lines_invoice ON dbo.invoice_lines (invoice_id);
GO

-- ============================================================================
-- validation_results
-- ============================================================================
CREATE TABLE dbo.validation_results (
    id                BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    invoice_id        NVARCHAR(64)    NOT NULL,
    rule_id           NVARCHAR(16)    NOT NULL,
    severity           NVARCHAR(8)     NOT NULL,
    passed             BIT             NOT NULL,
    message            NVARCHAR(1024)  NOT NULL,
    fields             NVARCHAR(MAX)   NULL,
    suggested_value    NVARCHAR(MAX)   NULL,
    evaluated_at        DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_validation_results_invoice FOREIGN KEY (invoice_id) REFERENCES dbo.invoices (invoice_id),
    CONSTRAINT CK_validation_results_severity CHECK (severity IN ('hard', 'soft')),
    CONSTRAINT CK_validation_results_fields_json CHECK (fields IS NULL OR ISJSON(fields) = 1)
);
GO
CREATE INDEX IX_validation_results_invoice ON dbo.validation_results (invoice_id);
GO

-- ============================================================================
-- purchase_orders / purchase_order_lines / goods_receipts
-- Mirrored from the customer's ERP export (assumption A-01: at least daily).
-- ============================================================================
CREATE TABLE dbo.purchase_orders (
    po_number    NVARCHAR(64)    NOT NULL PRIMARY KEY,
    vendor_id    NVARCHAR(64)    NOT NULL,
    is_closed    BIT             NOT NULL DEFAULT 0,
    synced_at    DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_purchase_orders_vendor FOREIGN KEY (vendor_id) REFERENCES dbo.vendors (vendor_id)
);
GO
CREATE INDEX IX_purchase_orders_vendor ON dbo.purchase_orders (vendor_id);
GO

CREATE TABLE dbo.purchase_order_lines (
    po_line_id          NVARCHAR(64)    NOT NULL PRIMARY KEY,
    po_number            NVARCHAR(64)    NOT NULL,
    line_number          INT             NOT NULL,
    product_code         NVARCHAR(64)    NULL,
    unit_price           DECIMAL(19, 2)  NOT NULL,
    quantity_ordered      DECIMAL(19, 4)  NOT NULL,
    quantity_received     DECIMAL(19, 4)  NOT NULL DEFAULT 0,
    quantity_invoiced     DECIMAL(19, 4)  NOT NULL DEFAULT 0,
    CONSTRAINT FK_po_lines_po FOREIGN KEY (po_number) REFERENCES dbo.purchase_orders (po_number),
    CONSTRAINT UQ_po_lines_po_line UNIQUE (po_number, line_number)
);
GO
CREATE INDEX IX_po_lines_po ON dbo.purchase_order_lines (po_number);
GO
CREATE INDEX IX_po_lines_product_code ON dbo.purchase_order_lines (product_code) WHERE product_code IS NOT NULL;
GO

-- Individual goods-receipt documents backing purchase_order_lines.quantity_received —
-- kept for audit traceability (US-10) even though core.matching.three_way only ever reads
-- the already-aggregated quantity_received_unbilled on the PO line.
CREATE TABLE dbo.goods_receipts (
    goods_receipt_id   BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    po_line_id          NVARCHAR(64)    NOT NULL,
    grn_number           NVARCHAR(64)    NULL,
    quantity_received    DECIMAL(19, 4)  NOT NULL,
    received_at          DATE            NOT NULL,
    synced_at            DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_goods_receipts_po_line FOREIGN KEY (po_line_id) REFERENCES dbo.purchase_order_lines (po_line_id)
);
GO
CREATE INDEX IX_goods_receipts_po_line ON dbo.goods_receipts (po_line_id);
GO

-- ============================================================================
-- review_assignments
-- ============================================================================
CREATE TABLE dbo.review_assignments (
    invoice_id     NVARCHAR(64)    NOT NULL PRIMARY KEY,
    claimed_by      NVARCHAR(256)   NULL,
    claimed_at      DATETIME2(3)    NULL,
    expires_at      DATETIME2(3)    NULL,
    submitted_at    DATETIME2(3)    NULL,
    outcome          NVARCHAR(16)    NULL,
    CONSTRAINT FK_review_assignments_invoice FOREIGN KEY (invoice_id) REFERENCES dbo.invoices (invoice_id),
    CONSTRAINT CK_review_assignments_outcome CHECK (outcome IS NULL OR outcome IN ('approved', 'rejected'))
);
GO

-- FR-062: the lock-expiry sweeper's whole query.
CREATE INDEX IX_review_assignments_expires_at ON dbo.review_assignments (expires_at) WHERE submitted_at IS NULL;
GO

-- ============================================================================
-- corrections
-- ============================================================================
CREATE TABLE dbo.corrections (
    id                     BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    invoice_id             NVARCHAR(64)    NOT NULL,
    field_name             NVARCHAR(64)    NOT NULL,
    model_value            NVARCHAR(MAX)   NULL,
    human_value            NVARCHAR(MAX)   NULL,
    model_id               NVARCHAR(64)    NULL,
    model_version          NVARCHAR(32)    NULL,
    corrected_by           NVARCHAR(256)   NOT NULL,
    corrected_at           DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    used_in_training_run   NVARCHAR(64)    NULL,
    CONSTRAINT FK_corrections_invoice FOREIGN KEY (invoice_id) REFERENCES dbo.invoices (invoice_id)
);
GO
CREATE INDEX IX_corrections_invoice ON dbo.corrections (invoice_id);
GO
-- FR-071: the training-set builder's query — corrections not yet used in a training run.
CREATE INDEX IX_corrections_unused ON dbo.corrections (used_in_training_run) WHERE used_in_training_run IS NULL;
GO

-- ============================================================================
-- audit_log (append-only — see 0002_audit_log_grants.sql for the enforcement)
-- ============================================================================
CREATE TABLE dbo.audit_log (
    id             BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    entity_type     NVARCHAR(32)    NOT NULL,
    entity_id       NVARCHAR(64)    NOT NULL,
    action          NVARCHAR(64)    NOT NULL,
    actor           NVARCHAR(256)   NOT NULL,
    actor_role       NVARCHAR(32)    NOT NULL,
    before          NVARCHAR(MAX)   NULL,
    after           NVARCHAR(MAX)   NULL,
    reason          NVARCHAR(512)   NULL,
    occurred_at     DATETIME2(3)    NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT CK_audit_log_before_json CHECK (before IS NULL OR ISJSON(before) = 1),
    CONSTRAINT CK_audit_log_after_json CHECK (after IS NULL OR ISJSON(after) = 1)
);
GO
CREATE INDEX IX_audit_log_entity ON dbo.audit_log (entity_type, entity_id);
GO
CREATE INDEX IX_audit_log_occurred_at ON dbo.audit_log (occurred_at);
GO

-- ============================================================================
-- model_registry
-- ============================================================================
CREATE TABLE dbo.model_registry (
    model_id                NVARCHAR(64)    NOT NULL PRIMARY KEY,
    alias                   NVARCHAR(16)    NOT NULL,
    trained_at               DATETIME2(3)    NOT NULL,
    training_run_id          NVARCHAR(64)    NULL,
    macro_f1                FLOAT           NULL,
    document_exact_match     FLOAT           NULL,
    promoted_by              NVARCHAR(256)   NULL,
    promoted_at               DATETIME2(3)    NULL,
    is_active                 BIT             NOT NULL DEFAULT 0,
    CONSTRAINT CK_model_registry_alias CHECK (alias IN ('prod', 'candidate'))
);
GO
