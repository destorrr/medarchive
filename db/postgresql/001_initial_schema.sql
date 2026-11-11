-- МедАрхив: PostgreSQL target schema (draft)
-- PostgreSQL 15+
-- This is a target-schema draft, not a production migration.
-- Review against the approved route matrix and resolve open workflow decisions first.

BEGIN;

CREATE TABLE branches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE locations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('branch', 'central_office', 'archive')),
    branch_id uuid REFERENCES branches(id) ON DELETE RESTRICT,
    name text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_location_branch_binding CHECK (
        (kind = 'branch' AND branch_id IS NOT NULL) OR
        (kind <> 'branch' AND branch_id IS NULL)
    )
);
CREATE UNIQUE INDEX uq_locations_branch ON locations(branch_id) WHERE kind = 'branch';
CREATE UNIQUE INDEX uq_locations_singleton_kind ON locations(kind)
    WHERE kind IN ('central_office', 'archive');

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    full_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('branch', 'central_office', 'archive', 'admin', 'viewer')),
    branch_id uuid REFERENCES branches(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_user_branch_binding CHECK (
        (role = 'branch' AND branch_id IS NOT NULL) OR
        (role <> 'branch' AND branch_id IS NULL)
    )
);

CREATE TABLE couriers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name text NOT NULL,
    phone text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cards (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_number text NOT NULL UNIQUE,
    full_name text NOT NULL,
    date_of_birth date NOT NULL,
    phone text,
    origin_branch_id uuid NOT NULL REFERENCES branches(id) ON DELETE RESTRICT,
    current_location_id uuid REFERENCES locations(id) ON DELETE RESTRICT,
    current_shipment_id uuid,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'under_review')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_card_exactly_one_location CHECK (
        (current_location_id IS NOT NULL AND current_shipment_id IS NULL) OR
        (current_location_id IS NULL AND current_shipment_id IS NOT NULL)
    )
);

CREATE TABLE boxes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    box_number text NOT NULL UNIQUE,
    current_location_id uuid REFERENCES locations(id) ON DELETE RESTRICT,
    current_shipment_id uuid,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'written_off', 'under_review')),
    created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_box_exactly_one_location CHECK (
        (current_location_id IS NOT NULL AND current_shipment_id IS NULL) OR
        (current_location_id IS NULL AND current_shipment_id IS NOT NULL)
    )
);

CREATE TABLE box_cycles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    box_id uuid NOT NULL REFERENCES boxes(id) ON DELETE RESTRICT,
    cycle_number integer NOT NULL CHECK (cycle_number > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    started_by uuid REFERENCES users(id) ON DELETE SET NULL,
    ended_by uuid REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (box_id, cycle_number),
    CONSTRAINT ck_box_cycle_dates CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE UNIQUE INDEX uq_box_one_open_cycle ON box_cycles(box_id) WHERE ended_at IS NULL;

CREATE TABLE box_card_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    box_cycle_id uuid NOT NULL REFERENCES box_cycles(id) ON DELETE RESTRICT,
    card_id uuid NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
    added_at timestamptz NOT NULL DEFAULT now(),
    added_by uuid REFERENCES users(id) ON DELETE SET NULL,
    removed_at timestamptz,
    removed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_box_membership_dates CHECK (removed_at IS NULL OR removed_at >= added_at)
);
CREATE UNIQUE INDEX uq_card_one_active_box ON box_card_memberships(card_id)
    WHERE removed_at IS NULL;

CREATE TABLE shipments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    from_location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    to_location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    courier_id uuid NOT NULL REFERENCES couriers(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_transit', 'received', 'exception', 'cancelled')),
    issued_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    handed_over_at timestamptz,
    received_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    received_at timestamptz,
    exception_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    exception_at timestamptz,
    exception_note text,
    cancelled_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    cancelled_at timestamptz,
    CONSTRAINT ck_shipment_distinct_locations CHECK (from_location_id <> to_location_id),
    CONSTRAINT ck_shipment_handover_time CHECK (
        (status IN ('draft', 'cancelled') AND handed_over_at IS NULL) OR
        (status IN ('in_transit', 'received', 'exception') AND handed_over_at IS NOT NULL)
    ),
    CONSTRAINT ck_shipment_received_fields CHECK (
        (status = 'received' AND received_by IS NOT NULL AND received_at IS NOT NULL) OR
        (status <> 'received' AND received_by IS NULL AND received_at IS NULL)
    )
);

ALTER TABLE cards ADD CONSTRAINT fk_card_current_shipment
    FOREIGN KEY (current_shipment_id) REFERENCES shipments(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE boxes ADD CONSTRAINT fk_box_current_shipment
    FOREIGN KEY (current_shipment_id) REFERENCES shipments(id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE shipment_cards (
    shipment_id uuid NOT NULL REFERENCES shipments(id) ON DELETE RESTRICT,
    card_id uuid NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
    PRIMARY KEY (shipment_id, card_id),
    UNIQUE (card_id, shipment_id)
);
CREATE UNIQUE INDEX uq_card_one_active_shipment
    ON cards(current_shipment_id, id) WHERE current_shipment_id IS NOT NULL;

CREATE TABLE shipment_boxes (
    shipment_id uuid NOT NULL REFERENCES shipments(id) ON DELETE RESTRICT,
    box_id uuid NOT NULL REFERENCES boxes(id) ON DELETE RESTRICT,
    PRIMARY KEY (shipment_id, box_id),
    UNIQUE (box_id, shipment_id)
);

-- A non-NULL current_shipment_id must point to a manifest row for that exact object.
-- MATCH SIMPLE means the composite FK is not checked while the object is stationary.
ALTER TABLE cards ADD CONSTRAINT fk_card_shipment_manifest
    FOREIGN KEY (id, current_shipment_id)
    REFERENCES shipment_cards(card_id, shipment_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE boxes ADD CONSTRAINT fk_box_shipment_manifest
    FOREIGN KEY (id, current_shipment_id)
    REFERENCES shipment_boxes(box_id, shipment_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX uq_box_one_active_shipment
    ON boxes(current_shipment_id, id) WHERE current_shipment_id IS NOT NULL;

CREATE TABLE archive_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id uuid NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
    requested_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'in_progress', 'completed', 'not_found', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    fulfilled_by uuid REFERENCES users(id) ON DELETE RESTRICT,
    result_note text
);
CREATE UNIQUE INDEX uq_card_one_active_archive_request ON archive_requests(card_id)
    WHERE status IN ('new', 'in_progress');

CREATE TABLE archive_shelves (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    section_name text NOT NULL,
    shelf_name text NOT NULL,
    qr_code text UNIQUE,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (section_name, shelf_name)
);

CREATE TABLE box_placements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    box_id uuid NOT NULL REFERENCES boxes(id) ON DELETE RESTRICT,
    shelf_id uuid NOT NULL REFERENCES archive_shelves(id) ON DELETE RESTRICT,
    placed_at timestamptz NOT NULL DEFAULT now(),
    placed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    removed_at timestamptz,
    removed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_placement_dates CHECK (removed_at IS NULL OR removed_at >= placed_at)
);
CREATE UNIQUE INDEX uq_box_one_active_placement ON box_placements(box_id)
    WHERE removed_at IS NULL;

CREATE TABLE domain_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    actor_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    correction_of_event_id bigint REFERENCES domain_events(id) ON DELETE RESTRICT,
    request_id text
);
CREATE INDEX ix_domain_events_aggregate ON domain_events(aggregate_type, aggregate_id, id);
CREATE INDEX ix_domain_events_occurred_at ON domain_events(occurred_at);

CREATE INDEX ix_cards_full_name ON cards USING gin (to_tsvector('simple', full_name));
CREATE INDEX ix_cards_current_location ON cards(current_location_id);
CREATE INDEX ix_cards_current_shipment ON cards(current_shipment_id);
CREATE INDEX ix_boxes_current_location ON boxes(current_location_id);
CREATE INDEX ix_shipments_route_status ON shipments(from_location_id, to_location_id, status);
CREATE INDEX ix_archive_requests_status_created ON archive_requests(status, created_at);
CREATE INDEX ix_box_memberships_cycle ON box_card_memberships(box_cycle_id, added_at);

COMMIT;

-- IMPORTANT IMPLEMENTATION NOTES:
-- 1. Route authorization, actor/location matching, Archive -> Central Office request
--    validation, manifest immutability, and whole-shipment acceptance require
--    transactional service logic and/or deferred constraint triggers.
-- 2. A card transported inside a box must be represented consistently: the service
--    must move the box and all active member cards to the same shipment atomically.
-- 3. The composite manifest FKs ensure that a non-NULL current_shipment_id matches
--    an explicit shipment_cards/shipment_boxes row.
-- 4. Do not enable direct UPDATE/DELETE on domain_events for the application role.
-- 5. Add PostgreSQL integration tests before using this schema for production.
