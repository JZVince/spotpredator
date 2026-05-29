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
        # Append SUMMARY3 chunks, overwrite SUMMARY1/2
        if ($1 eq 'SUMMARY3' && exists $summary{SUMMARY3}) {
            $summary{SUMMARY3} .= ' ' . $2;
        } else {
            $summary{$1} = $2;
        }
    }
}
close($fh);

# Parse SUMMARY3 hourly data: "6h:23% 7h:25% 8h:30%..."
if (exists $summary{SUMMARY3}) {
    while ($summary{SUMMARY3} =~ /(\d+)h:(\d+)%/g) {
        $hourly_predator{$1} = $2;
    }
}

# Build plain text report (saved to file)
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

# Field scan summary
$report .= "FIELD SCAN SUMMARY (via LoRa)\n";
$report .= "-" x 40 . "\n";
if (%summary) {
    $report .= "$summary{SUMMARY1}\n" if exists $summary{SUMMARY1};
    $report .= "$summary{SUMMARY2}\n" if exists $summary{SUMMARY2};
} else {
    $report .= "No scan summary received today.\n";
}
$report .= "\n" . "=" x 60 . "\n";

# Print to stdout
print $report;

# Save plain text to file
my $report_file = "/home/pi/spotpredator/data/logs/daily_report.txt";
open(my $out, '>', $report_file) or die "Cannot write report: $!";
print $out $report;
close($out);
print "Report saved to: $report_file\n";

# Build HTML email
my $html = build_html($today);

# Send email
if ($email_address && $email_password) {
    send_email($email_address, $email_password, $today, $html);
} else {
    print "Email not configured - skipping\n";
}

# Build HTML email body
sub build_html {
    my ($date) = @_;
    my $time = get_time();
    my $alerts_count = scalar(@predator_alerts);
    my $hb_count = scalar(@heartbeats);

    # Alert color
    my $header_color = $alerts_count > 0 ? '#c0392b' : '#2c7a2c';
    my $status_text  = $alerts_count > 0 ? "$alerts_count PREDATOR ALERT(S) TODAY" : "All Clear - No Predators Detected";

    # Predator alerts rows
    my $alert_rows = '';
    if (@predator_alerts) {
        for my $a (@predator_alerts) {
            my $conf_pct = int($a->{confidence} * 100);
            $alert_rows .= "<tr><td>$a->{time}</td><td>$a->{type}</td><td>${conf_pct}%</td></tr>\n";
        }
    } else {
        $alert_rows = '<tr><td colspan="3" style="color:#666;">No predator alerts today.</td></tr>';
    }

    # Heartbeat section
    my $hb_html = '';
    if (@heartbeats) {
        $hb_html = "<p><strong>Received:</strong> $hb_count</p>
        <p><strong>First:</strong> $heartbeats[0]{time} &mdash; $heartbeats[0]{status}</p>
        <p><strong>Last:</strong> $heartbeats[-1]{time} &mdash; $heartbeats[-1]{status}</p>";
    } else {
        $hb_html = '<p style="color:#666;">No heartbeats received today.</p>';
    }

    # Scan summary
    my $summary_html = '';
    if (%summary) {
        $summary_html .= "<p>$summary{SUMMARY1}</p>" if exists $summary{SUMMARY1};
        $summary_html .= "<p>$summary{SUMMARY2}</p>" if exists $summary{SUMMARY2};
    } else {
        $summary_html = '<p style="color:#666;">No scan summary received today.</p>';
    }

    # HTML bar chart
    my $chart_html = '';
    if (%hourly_predator) {
        my @hours = sort { $a <=> $b } keys %hourly_predator;
        $chart_html .= '<table style="border-collapse:collapse;width:100%;max-width:600px;">';
        $chart_html .= '<tr><th style="text-align:left;padding:4px;">Hour</th><th style="text-align:left;padding:4px;">Predator Confidence</th><th style="padding:4px;">%</th></tr>';
        for my $h (@hours) {
            my $val = $hourly_predator{$h};
            my $bar_color = $val >= 85 ? '#c0392b' : $val >= 50 ? '#e67e22' : '#2c7a2c';
            my $bar_width = $val * 3;  # scale to max ~300px
            $chart_html .= "<tr>
                <td style='padding:4px;white-space:nowrap;'>${h}:00</td>
                <td style='padding:4px;width:100%;'>
                    <div style='background:$bar_color;width:${bar_width}px;height:20px;border-radius:3px;'></div>
                </td>
                <td style='padding:4px;text-align:right;'>${val}%</td>
            </tr>\n";
        }
        $chart_html .= '</table>';
    } else {
        $chart_html = '<p style="color:#666;">No hourly data available.</p>';
    }

    return <<HTML;
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:650px;margin:0 auto;padding:20px;color:#333;">

  <div style="background:$header_color;color:white;padding:16px 20px;border-radius:6px;margin-bottom:20px;">
    <h2 style="margin:0;">SpotPredator Daily Report</h2>
    <p style="margin:4px 0 0;">$date &mdash; Generated at $time</p>
  </div>

  <div style="background:$header_color;color:white;padding:10px 20px;border-radius:6px;margin-bottom:20px;text-align:center;font-size:18px;font-weight:bold;">
    $status_text
  </div>

  <h3 style="border-bottom:2px solid #ddd;padding-bottom:6px;">Predator Alerts</h3>
  <table style="width:100%;border-collapse:collapse;">
    <tr style="background:#f5f5f5;">
      <th style="text-align:left;padding:8px;">Time</th>
      <th style="text-align:left;padding:8px;">Type</th>
      <th style="text-align:left;padding:8px;">Confidence</th>
    </tr>
    $alert_rows
  </table>

  <h3 style="border-bottom:2px solid #ddd;padding-bottom:6px;margin-top:24px;">Heartbeats</h3>
  $hb_html

  <h3 style="border-bottom:2px solid #ddd;padding-bottom:6px;margin-top:24px;">Field Scan Summary</h3>
  $summary_html

  <h3 style="border-bottom:2px solid #ddd;padding-bottom:6px;margin-top:24px;">Predator Confidence by Hour</h3>
  $chart_html

  <p style="margin-top:30px;font-size:12px;color:#999;">SpotPredator &mdash; Farm Predator Detection System</p>
</body>
</html>
HTML
}

# Send HTML email via Gmail SMTP SSL
sub send_email {
    my ($from, $password, $date, $html_body) = @_;

    my $subject = "SpotPredator Report - $date";
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
        $smtp->datasend("From: $from\r\n");
        $smtp->datasend("To: $from\r\n");
        $smtp->datasend("Subject: $subject\r\n");
        $smtp->datasend("MIME-Version: 1.0\r\n");
        $smtp->datasend("Content-Type: text/html; charset=UTF-8\r\n");
        $smtp->datasend("\r\n");
        $smtp->datasend($html_body);
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
