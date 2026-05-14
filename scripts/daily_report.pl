#!/usr/bin/perl
use strict;
use warnings;
use POSIX qw(floor);
use Net::SMTP;

# SpotPredator Daily Report
# Reads field_messages.log from display station and generates a summary report
# Run daily at 10:00 PM via cron: 0 22 * * * perl /home/pi/spotpredator/scripts/daily_report.pl

my $log_file  = "/home/pi/spotpredator/data/logs/field_messages.log";
my $env_file  = "/home/pi/spotpredator/display_station/.env";
my $today     = get_today();

# Load email credentials from .env
my ($email_address, $email_password) = load_env($env_file);

# Data structures
my @heartbeats;
my @predator_alerts;
my %summary;          # SUMMARY1, SUMMARY2, SUMMARY3
my %hourly_predator;  # hour -> predator confidence from SUMMARY3

# Parse log file
open(my $fh, '<', $log_file) or die "Cannot open $log_file: $!";
while (my $line = <$fh>) {
    chomp $line;
    next unless $line =~ /^$today/;  # Only today's entries

    if ($line =~ /HEARTBEAT \| (.+) \| (\d{2}:\d{2})/) {
        push @heartbeats, { status => $1, time => $2 };
    }
    elsif ($line =~ /PREDATOR \| (\w+) \| confidence=([\d.]+) \| (\S+)/) {
        push @predator_alerts, { type => $1, confidence => $2, time => $3 };
    }
    elsif ($line =~ /(SUMMARY[123]) \| (.+)/) {
        $summary{$1} = $2;
    }
}
close($fh);

# Parse SUMMARY3 hourly data: "6h:23% 7h:25% 8h:30%..."
if (exists $summary{SUMMARY3}) {
    while ($summary{SUMMARY3} =~ /(\d+)h:(\d+)%/g) {
        $hourly_predator{$1} = $2;
    }
}

# Build report
my $report = "";
$report .= "=" x 60 . "\n";
$report .= "SpotPredator Daily Report - $today\n";
$report .= "Generated at: " . get_time() . "\n";
$report .= "=" x 60 . "\n\n";

# Predator alerts
$report .= "PREDATOR ALERTS\n";
$report .= "-" x 40 . "\n";
if (@predator_alerts) {
    $report .= scalar(@predator_alerts) . " alert(s) detected today!\n";
    for my $alert (@predator_alerts) {
        my $conf_pct = int($alert->{confidence} * 100);
        $report .= "  $alert->{time} - $alert->{type} ($conf_pct%)\n";
    }
} else {
    $report .= "No predator alerts today.\n";
}
$report .= "\n";

# Heartbeats
$report .= "HEARTBEATS\n";
$report .= "-" x 40 . "\n";
if (@heartbeats) {
    $report .= "Received: " . scalar(@heartbeats) . "\n";
    $report .= "First:    $heartbeats[0]{time} - $heartbeats[0]{status}\n";
    $report .= "Last:     $heartbeats[-1]{time} - $heartbeats[-1]{status}\n";
} else {
    $report .= "No heartbeats received today.\n";
}
$report .= "\n";

# Field scan summary from LoRa
$report .= "FIELD SCAN SUMMARY (via LoRa)\n";
$report .= "-" x 40 . "\n";
if (%summary) {
    $report .= "$summary{SUMMARY1}\n" if exists $summary{SUMMARY1};
    $report .= "$summary{SUMMARY2}\n" if exists $summary{SUMMARY2};
} else {
    $report .= "No scan summary received today.\n";
}
$report .= "\n";

# ASCII graph: hourly predator confidence
$report .= "PREDATOR CONFIDENCE BY HOUR (ASCII Graph)\n";
$report .= "-" x 40 . "\n";
if (%hourly_predator) {
    my $max_conf = 100;
    my $bar_height = 10;  # number of rows in graph

    # Get sorted hours
    my @hours = sort { $a <=> $b } keys %hourly_predator;

    # Draw graph rows from top to bottom
    for my $row (reverse 1..$bar_height) {
        my $threshold = ($row / $bar_height) * $max_conf;
        my $label = sprintf("%3d%% |", int($threshold));
        $report .= $label;
        for my $h (@hours) {
            my $val = $hourly_predator{$h} // 0;
            $report .= $val >= $threshold ? "  ## " : "     ";
        }
        $report .= "\n";
    }

    # X axis
    $report .= "     +" . "-" x (scalar(@hours) * 5) . "\n";

    # Hour labels
    $report .= "      ";
    for my $h (@hours) {
        $report .= sprintf(" %02dh ", $h);
    }
    $report .= "\n";
} else {
    $report .= "No hourly data available.\n";
}

$report .= "\n" . "=" x 60 . "\n";

# Print to stdout
print $report;

# Save to file (overwrites previous)
my $report_file = "/home/pi/spotpredator/data/logs/daily_report.txt";
open(my $out, '>', $report_file) or die "Cannot write report: $!";
print $out $report;
close($out);
print "Report saved to: $report_file\n";

# Send email
if ($email_address && $email_password) {
    send_email($email_address, $email_password, $today, $report);
} else {
    print "Email not configured - skipping\n";
}

# Send email via Gmail SMTP SSL
sub send_email {
    my ($from, $password, $date, $body) = @_;

    my $subject = "SpotPredator Perl Report - $date";
    my $alerts  = scalar(@predator_alerts);
    $subject   .= " - $alerts alert(s)" if $alerts > 0;

    eval {
        my $smtp = Net::SMTP->new(
            'smtp.gmail.com',
            Port    => 465,
            SSL     => 1,
            Timeout => 30,
        );
        die "Could not connect to Gmail SMTP" unless $smtp;

        $smtp->auth($from, $password) or die "Authentication failed";

        $smtp->mail($from);
        $smtp->to($from);
        $smtp->data();
        $smtp->datasend("From: $from\n");
        $smtp->datasend("To: $from\n");
        $smtp->datasend("Subject: $subject\n");
        $smtp->datasend("Content-Type: text/plain; charset=UTF-8\n");
        $smtp->datasend("\n");
        $smtp->datasend($body);
        $smtp->dataend();
        $smtp->quit();

        print "Email sent to $from\n";
    };
    if ($@) {
        print "Failed to send email: $@\n";
    }
}

# Load EMAIL_ADDRESS and EMAIL_PASSWORD from .env file
sub load_env {
    my ($path) = @_;
    my ($addr, $pass) = ('', '');
    open(my $ef, '<', $path) or return ($addr, $pass);
    while (my $line = <$ef>) {
        chomp $line;
        $line =~ s/^\s+|\s+$//g;
        next if $line =~ /^#/ or $line !~ /=/;
        my ($key, $val) = split(/=/, $line, 2);
        $key =~ s/^\s+|\s+$//g;
        $val =~ s/^\s+|\s+$//g;
        $addr = $val if $key eq 'EMAIL_ADDRESS';
        $pass = $val if $key eq 'EMAIL_PASSWORD';
    }
    close($ef);
    return ($addr, $pass);
}

# Helper: get today's date as YYYY-MM-DD
sub get_today {
    my @t = localtime(time);
    return sprintf("%04d-%02d-%02d", $t[5]+1900, $t[4]+1, $t[3]);
}

# Helper: get current time as HH:MM
sub get_time {
    my @t = localtime(time);
    return sprintf("%02d:%02d", $t[2], $t[1]);
}
